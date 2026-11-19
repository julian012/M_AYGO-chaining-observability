"""Une el bus con un core que NO recibe el ID del bus (tipo Stratus, ``CORE_LINK=tracenumber``).

El core registra su propio tracenumber (``CORE-<12 dígitos>``) sin padre. El bus registra, en su línea de
respuesta, ese mismo tracenumber según su política (``obsproto/tracelink.py``). Este módulo empareja las dos
mitades y agrega el eslabón bus -> core al grafo:

  clear  igualdad exacta del tracenumber                    determinista
  token  HMAC del tracenumber del core == token del bus      determinista, sin ver el valor en el log del bus
  mask   mismos últimos dígitos y hora cercana (ventana)     inferencia: SOLO se une si hay un único candidato;
                                                             con dos o más la referencia queda "ambigua" y la
                                                             traza se reporta incompleta (nunca se adivina)

Las referencias que no encuentran pareja se reintentan mientras llegan más eventos (los logs se leen a ritmos
distintos) y se dan por perdidas cuando la hora de los eventos ya superó su ventana.
"""

from __future__ import annotations

import bisect
import re
import threading
from collections import OrderedDict, deque
from typing import Deque, Dict, List, Tuple

from obsproto import tracelink
from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import Event, TraceRef

CORE_TRACE_ID = re.compile(r"^CORE-([0-9]{12})\Z")


class CoreLinker:
    def __init__(self, graph: IdGraph, key: str = "", window_s: float = 5.0, max_items: int = 300_000) -> None:
        self.graph, self.key, self.window = graph, key, window_s
        self.max_items = max_items
        self._by_value: OrderedDict[str, str] = OrderedDict()  # tracenumber o token -> CORE-id
        self._times: List[Tuple[float, str, str]] = []  # (hora, tracenumber, CORE-id), ordenada por hora
        self._linked: OrderedDict[str, None] = OrderedDict()  # CORE-ids ya unidos (no se reutilizan)
        self._pending: Deque[TraceRef] = deque()
        self._newest = 0.0  # hora del evento más reciente visto (de cualquier fuente)
        self._newest_core = 0.0  # hora del registro del core más reciente: hasta dónde se ha leído su log
        self._clock = 0.0  # reloj real, solo válido cuando el log del core está al día
        self._lock = threading.Lock()
        self.counts: Dict[str, int] = {"refs": 0, "linked": 0, "ambiguous": 0, "unmatched": 0}

    # -- entrada -------------------------------------------------------------------------------------- #
    def on_core_event(self, ev: Event) -> None:
        m = CORE_TRACE_ID.match(ev.id)
        if ev.parent_id or not m:
            return  # el core ya trae su padre (modo frame) o no es un tracenumber
        tn = m[1]
        with self._lock:
            self._by_value[tn] = ev.id
            if self.key:
                self._by_value[tracelink.token(tn, self.key)] = ev.id
            bisect.insort(self._times, (ev.end_ts, tn, ev.id))
            self._newest = max(self._newest, ev.end_ts)
            self._newest_core = max(self._newest_core, ev.end_ts)
            while len(self._by_value) > 2 * self.max_items:
                self._by_value.popitem(last=False)
            if len(self._times) > self.max_items * 1.1:  # se recorta en bloque (borrar al inicio es O(n))
                del self._times[: len(self._times) - self.max_items]

    def on_ref(self, ref: TraceRef) -> None:
        with self._lock:
            self.counts["refs"] += 1
            self._pending.append(ref)
            self._newest = max(self._newest, ref.ts)

    # -- unión ---------------------------------------------------------------------------------------- #
    def resolve(self, now: float = 0.0, core_caught_up: bool = False) -> None:
        """Intenta unir las referencias pendientes. Se llama después de cada ronda de lectura de logs.

        ``core_caught_up``: el log del core ya se leyó hasta el final. Solo entonces el reloj (``now``) sirve
        para dar una ventana por cerrada; mientras se relee un log viejo, solo cuenta la hora de los eventos.
        """
        with self._lock:
            self._clock = now if core_caught_up else 0.0
            still: Deque[TraceRef] = deque()
            while self._pending:
                ref = self._pending.popleft()
                outcome = self._try(ref)
                if outcome == "wait":
                    if max(self._newest, self._clock) - ref.ts > max(3 * self.window, 30.0):
                        self.counts["unmatched"] += 1  # el core nunca apareció en su ventana
                    else:
                        still.append(ref)
                else:
                    self.counts[outcome] += 1
            self._pending = still

    def _try(self, ref: TraceRef) -> str:
        k = tracelink.kind(ref.value)
        if k in ("clear", "token"):
            core_id = self._by_value.get(ref.value)
            return self._link(ref.bus_id, core_id) if core_id else "wait"
        # Máscara: candidatos con los mismos últimos dígitos dentro de la ventana de tiempo.
        suffix = ref.value.lstrip("*")
        lo = bisect.bisect_left(self._times, (ref.ts - self.window, "", ""))
        hi = bisect.bisect_right(self._times, (ref.ts + self.window, "~", "~"))
        cands = [cid for _, tn, cid in self._times[lo:hi] if tn.endswith(suffix) and cid not in self._linked]
        if max(self._newest_core, self._clock - 1.0) < ref.ts + self.window:
            return "wait"  # el log del core aún no pasa el final de la ventana: pueden llegar candidatos
        if len(cands) == 1:
            return self._link(ref.bus_id, cands[0])
        return "ambiguous" if cands else "unmatched"

    def _link(self, bus_id: str, core_id: str) -> str:
        if self.graph.link(core_id, bus_id):
            self._linked[core_id] = None
            while len(self._linked) > self.max_items:
                self._linked.popitem(last=False)
            return "linked"
        return "wait"  # el registro del core aún no llegó al grafo (o ya fue descartado)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {**self.counts, "pending": len(self._pending)}
