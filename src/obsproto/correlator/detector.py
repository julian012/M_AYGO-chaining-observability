"""Detector de anomalías sobre la telemetría centralizada: de la reacción a la prevención.

Evalúa cada ``DETECT_EVERY_S`` segundos, sobre ventanas recientes, tres reglas. Cada alerta nombra la capa:

  source_silent:<capa>  el gateway sigue recibiendo tráfico pero de esa fuente no llegan líneas (o no se puede
                        leer): la fuente dejó de transmitir. Se distingue de un servicio caído porque el negocio
                        sigue operando.
  chain_break:<capa>    las líneas llegan, pero las trazas recientes quedan incompletas y esa es la primera capa
                        que falta (p. ej. el bus dejó de escribir DEBUG, o el MFT no movió los archivos).
  latency:<capa>        la p95 de duración de la capa supera 3 veces su línea base (y al menos 100 ms más). Si varias
                        capas suben a la vez, se señala la más profunda: un core lento hace lentos al bus y al gateway.

Una alerta se dispara en la primera evaluación que cumple la condición y se resuelve tras dos evaluaciones seguidas sin
cumplirla. El detector no evalúa hasta que todas las fuentes se leyeron completas y pasaron ``warmup_s`` segundos
(línea base).
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

INLINE = ("gateway", "bus", "core")
ASYNC = ("mft", "modern")


class Detector:
    def __init__(
        self,
        graph,
        sources: Callable[[], Dict[str, dict]],
        layers: Tuple[str, ...],
        warmup_s: float = 30.0,
        silent_after_s: Optional[Dict[str, float]] = None,
    ) -> None:
        self.graph, self.sources, self.layers = graph, sources, layers
        self.warmup_s = warmup_s
        self.silent_after = {"bus": 15.0, "core": 15.0, "mft": 25.0, "modern": 25.0, **(silent_after_s or {})}
        self.roots: Deque[Tuple[float, str]] = deque(maxlen=50_000)  # (hora del gateway, CLI-…)
        self.durations: Dict[str, Deque[Tuple[float, int]]] = {la: deque(maxlen=20_000) for la in INLINE}
        self._p95_history: Dict[str, Deque[float]] = {la: deque(maxlen=24) for la in INLINE}
        self._state: Dict[str, dict] = {}  # alerta -> {since, detail, misses}
        self.history: Deque[dict] = deque(maxlen=200)
        self._started = time.time()
        self._ready = False
        self._edge_since: Optional[float] = None  # desde cuándo hay tráfico en el borde
        self._lock = threading.Lock()

    # -- entrada desde el lector de logs ------------------------------------------------------------ #
    def on_event(self, ev) -> None:
        if ev.layer == "gateway":
            self.roots.append((ev.end_ts, ev.parent_id))
        if ev.layer in self.durations:
            self.durations[ev.layer].append((ev.end_ts, ev.duration_ms))

    # -- reglas -------------------------------------------------------------------------------------- #
    def _silent(self, now: float, src: Dict[str, dict]) -> Dict[str, str]:
        out = {}
        gw = src.get("gateway", {})
        if now - gw.get("lastLine", 0) > 15:  # sin tráfico en el borde no se puede hablar de fuente silenciosa
            self._edge_since = None
            return out
        if self._edge_since is None:  # el tráfico recién empieza (o vuelve): el silencio se mide desde aquí
            self._edge_since = now
        for layer, info in src.items():
            if layer == "gateway" or layer not in self.silent_after:
                continue
            silent_for = now - max(info.get("lastLine", 0), self._edge_since)
            if info.get("error"):
                out[layer] = f"no se puede leer: {info['error']}"
            elif silent_for > self.silent_after[layer]:
                out[layer] = f"sin líneas nuevas hace {silent_for:.0f} s"
        return out

    def _chain(self, now: float) -> Dict[str, str]:
        out = {}
        windows = [(INLINE, now - 20, now - 4)]
        if any(la in self.layers for la in ASYNC):
            windows.append((tuple(la for la in self.layers if la in ASYNC), now - 50, now - 22))
        for check, lo, hi in windows:
            sample = [cid for t, cid in list(self.roots) if lo <= t <= hi][-200:]
            missing: Dict[str, int] = {}
            n = 0
            for cid in sample:
                tr = self.graph.trace(cid)
                if tr is None:
                    continue
                gaps = [la for la in tr.missing_layers if la in check]
                if check is not INLINE and any(la in tr.missing_layers for la in INLINE):
                    continue  # ya cuenta en la ventana en línea
                n += 1
                if gaps:
                    missing[gaps[0]] = missing.get(gaps[0], 0) + 1
            for layer, k in missing.items():
                if n >= 10 and k / n >= 0.05:
                    out[layer] = f"{k} de {n} trazas recientes sin {layer}"
        return out

    def _latency(self, now: float) -> Dict[str, str]:
        flagged = {}
        for layer in INLINE:
            recent = sorted(d for t, d in self.durations[layer] if t >= now - 15)
            if len(recent) < 10:
                continue
            p95 = recent[min(len(recent) - 1, int(len(recent) * 0.95))]
            hist = self._p95_history[layer]
            base = statistics.median(hist) if len(hist) >= 6 else None
            if base is not None and p95 > max(3 * base, base + 100):
                flagged[layer] = (p95, base)
            elif not any(k.startswith("latency:") for k in self._state):
                hist.append(p95)  # la línea base solo aprende cuando no hay alerta de latencia
        for layer in reversed(INLINE):  # la capa más profunda afectada es la causa probable
            if layer in flagged:
                p95, base = flagged[layer]
                others = [la for la in flagged if la != layer]
                extra = f"; también afectadas: {', '.join(others)}" if others else ""
                return {layer: f"p95 {p95} ms frente a una línea base de {base:.0f} ms{extra}"}
        return {}

    # -- evaluación ----------------------------------------------------------------------------------- #
    def evaluate(self, now: Optional[float] = None, all_caught_up: bool = True) -> List[dict]:
        now = time.time() if now is None else now
        with self._lock:
            if not self._ready:
                if not all_caught_up or now - self._started < self.warmup_s:
                    self._latency(now)  # aprende la línea base durante el calentamiento
                    return []
                self._ready = True
            src = self.sources()
            found = {f"source_silent:{k}": v for k, v in self._silent(now, src).items()}
            found.update({f"chain_break:{k}": v for k, v in self._chain(now).items()})
            found.update({f"latency:{k}": v for k, v in self._latency(now).items()})
            for key, detail in found.items():
                st = self._state.get(key)
                if st is None:
                    self._state[key] = {"since": now, "detail": detail, "misses": 0}
                    self._emit("FIRING", key, now, detail)
                else:
                    st.update(detail=detail, misses=0)
            for key in [k for k in self._state if k not in found]:
                st = self._state[key]
                st["misses"] += 1
                if st["misses"] >= 2:
                    self._emit("RESOLVED", key, now, st["detail"], st["since"])
                    del self._state[key]
            return self.active()

    def _emit(self, state: str, key: str, now: float, detail: str, since: Optional[float] = None) -> None:
        rule, _, layer = key.partition(":")
        rec = {"state": state, "rule": rule, "layer": layer, "at": round(now, 3), "detail": detail}
        if since is not None:
            rec["firedAt"] = round(since, 3)
        self.history.append(rec)
        print("[alerta] " + json.dumps(rec, ensure_ascii=False), flush=True)

    def active(self) -> List[dict]:
        return [
            {"rule": k.split(":")[0], "layer": k.split(":")[1], "since": round(v["since"], 3), "detail": v["detail"]}
            for k, v in self._state.items()
        ]
