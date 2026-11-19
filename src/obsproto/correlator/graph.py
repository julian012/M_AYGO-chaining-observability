"""Grafo de IDs: reconstruye la traza end-to-end encadenando los pares (padre -> hijo).

Propiedades buscadas (cada una tiene prueba en tests/test_graph.py):
- independiente del orden de llegada de los eventos (los hijos terminan antes que los padres),
- idempotente ante duplicados,
- memoria acotada: al llenarse se descarta el evento MÁS VIEJO SEGÚN SU HORA, no el primero que llegó.
  Así, al releer logs a ritmos distintos tras un reinicio, se conservan completas las transacciones recientes
  (hallazgo de las pruebas de estrés en AWS; ver test_replay_at_uneven_speeds_keeps_recent_traces),
- una cadena rota se informa como traza incompleta (no se inventan enlaces).
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import threading
import time
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Set, Tuple

from obsproto.correlator.parsers import LAYER_ORDER, Event


@dataclass(frozen=True)
class Trace:
    trace_id: str  # 32 hex, compatible con el formato de trace-id de OpenTelemetry
    root_id: str
    events: List[Event]
    missing_layers: List[str]

    @property
    def complete(self) -> bool:
        return not self.missing_layers

    def to_dict(self) -> dict:
        t0 = min((e.end_ts - e.duration_ms / 1000 for e in self.events), default=0.0)
        return {
            "traceId": self.trace_id,
            "rootId": self.root_id,
            "complete": self.complete,
            "missingLayers": self.missing_layers,
            "spans": [
                {
                    "layer": e.layer,
                    "id": e.id,
                    "parentId": e.parent_id,
                    "startOffsetMs": max(0, int((e.end_ts - e.duration_ms / 1000 - t0) * 1000)),
                    "durationMs": e.duration_ms,
                    "status": e.status,
                    **({"otelTraceId": e.ext_trace_id} if e.ext_trace_id else {}),
                    **({"file": e.note} if e.note else {}),
                }
                for e in self.events
            ],
        }


class IdGraph:
    def __init__(self, max_events: int = 200_000, layers=None, side=None) -> None:
        self.max_events = max_events
        # Capas que debe tener una traza completa. Con un MFT se agrega "mft" (ver correlator/server.py).
        self.layers = tuple(layers) if layers else ("gateway", "bus", "core", "modern")
        # side(core_event) -> Event del MFT que movió el archivo con ese movimiento, o None.
        self.side = side
        self._events: Dict[str, Event] = {}
        self._parent_of: Dict[str, str] = {}
        self._children: Dict[str, Set[str]] = {}
        self._by_time: List[Tuple[float, int, str]] = []  # heap (hora del evento, orden de llegada, id)
        self._seq = itertools.count()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._events)

    def add(self, ev: Event) -> bool:
        """Devuelve False si el evento ya existía (duplicado)."""
        with self._lock:
            if ev.id in self._events:
                return False
            self._events[ev.id] = ev
            if ev.parent_id:  # un core de tramas no sabe quién lo llamó: queda sin padre hasta que se una
                self._parent_of[ev.id] = ev.parent_id
                self._children.setdefault(ev.parent_id, set()).add(ev.id)
            heapq.heappush(self._by_time, (ev.end_ts, next(self._seq), ev.id))
            while len(self._by_time) > self.max_events:
                self._evict(heapq.heappop(self._by_time)[2])
            return True

    def link(self, child_id: str, parent_id: str) -> bool:
        """Agrega un eslabón que no venía en el evento del hijo (p. ej. bus -> core por tracenumber).

        Solo se aplica a un hijo presente y sin padre: nunca reemplaza un eslabón existente.
        """
        with self._lock:
            ev = self._events.get(child_id)
            if ev is None or ev.parent_id:
                return False
            self._events[child_id] = replace(ev, parent_id=parent_id)
            self._parent_of[child_id] = parent_id
            self._children.setdefault(parent_id, set()).add(child_id)
            return True

    def _evict(self, event_id: str) -> None:
        ev = self._events.pop(event_id, None)
        if ev is None:
            return
        self._parent_of.pop(event_id, None)
        siblings = self._children.get(ev.parent_id)
        if siblings:
            siblings.discard(event_id)
            if not siblings:
                del self._children[ev.parent_id]

    def _root_of(self, any_id: str) -> str:
        seen = {any_id}
        node = any_id
        while node in self._parent_of:
            node = self._parent_of[node]
            if node in seen:  # defensa ante ciclos (datos corruptos)
                break
            seen.add(node)
        return node

    def trace(self, any_id: str) -> Optional[Trace]:
        """Traza completa a partir de CUALQUIER ID de cualquier capa."""
        with self._lock:
            if any_id not in self._events and any_id not in self._children:
                return None
            root = self._root_of(any_id)
            ids: List[str] = []
            visited = {root}
            stack = [root]
            while stack:
                node = stack.pop()
                if node in self._events:
                    ids.append(node)
                for child in self._children.get(node, ()):
                    if child not in visited:
                        visited.add(child)
                        stack.append(child)
            events = sorted(
                (self._events[i] for i in ids),
                key=lambda e: (LAYER_ORDER.index(e.layer), e.end_ts),
            )
            core = next((e for e in events if e.layer == "core"), None)
            if core is not None and core.status == "00" and self.side is not None:
                mft = self.side(core)  # transferencia del archivo plano: no es un hijo del grafo, se ubica por hora
                if mft is not None:
                    events = sorted(events + [mft], key=lambda e: (LAYER_ORDER.index(e.layer), e.end_ts))
            present = {e.layer for e in events}
            missing = [layer for layer in self.layers if layer not in present]
            if core is not None and core.status != "00":
                for layer in ("mft", "modern"):  # fallo del core: no hay novedad ni archivo (esperado)
                    if layer in missing:
                        missing.remove(layer)
            trace_id = hashlib.sha256(root.encode()).hexdigest()[:32]
            return Trace(trace_id, root, events, missing)

    def completeness(self, max_age_s: float = 0.0) -> Dict[str, int]:
        """Cuántas transacciones (raíces CLI-...) tienen traza completa.

        Recorre todas las trazas en memoria (O(n)). Con ``max_age_s`` devuelve el último resultado si es más reciente
        que eso: así ``/stats``, que el monitor consulta cada 10 s, no compite por CPU con ``/trace`` (hallazgo de las
        pruebas en AWS: con ~50 000 trazas en memoria, la consulta de una traza pasó de ~2 ms a ~18 ms).
        """
        now = time.monotonic()
        cached = getattr(self, "_comp_cache", None)
        if max_age_s and cached and now - cached[0] < max_age_s:
            return cached[1]
        with self._lock:
            roots = [p for p in self._children if p.startswith("CLI-")]
        total = complete = 0
        for root in roots:
            tr = self.trace(root)
            if tr is not None:
                total += 1
                complete += tr.complete
        result = {"total": total, "complete": complete}
        self._comp_cache = (now, result)
        return result
