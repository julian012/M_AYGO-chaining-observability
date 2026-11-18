"""Grafo de IDs: reconstruye la traza end-to-end encadenando los pares (padre -> hijo).

Propiedades buscadas (cada una tiene prueba en tests/test_graph.py):
- independiente del orden de llegada de los eventos (los hijos terminan antes que los padres),
- idempotente ante duplicados,
- memoria acotada (evicción FIFO),
- una cadena rota se informa como traza incompleta (no se inventan enlaces).
"""

from __future__ import annotations

import hashlib
import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Set

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
                }
                for e in self.events
            ],
        }


class IdGraph:
    def __init__(self, max_events: int = 200_000) -> None:
        self.max_events = max_events
        self._events: Dict[str, Event] = {}
        self._parent_of: Dict[str, str] = {}
        self._children: Dict[str, Set[str]] = {}
        self._order: Deque[str] = deque()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._events)

    def add(self, ev: Event) -> bool:
        """Devuelve False si el evento ya existía (duplicado)."""
        with self._lock:
            if ev.id in self._events:
                return False
            self._events[ev.id] = ev
            self._parent_of[ev.id] = ev.parent_id
            self._children.setdefault(ev.parent_id, set()).add(ev.id)
            self._order.append(ev.id)
            while len(self._order) > self.max_events:
                self._evict(self._order.popleft())
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
            present = {e.layer for e in events}
            missing = [layer for layer in LAYER_ORDER if layer not in present]
            core = next((e for e in events if e.layer == "core"), None)
            if core is not None and core.status != "00" and "modern" in missing:
                missing.remove("modern")  # fallo del core: el servicio posterior no se invoca (esperado)
            trace_id = hashlib.sha256(root.encode()).hexdigest()[:32]
            return Trace(trace_id, root, events, missing)

    def completeness(self) -> Dict[str, int]:
        """Cuántas transacciones (raíces CLI-...) tienen traza completa."""
        with self._lock:
            roots = [p for p in self._children if p.startswith("CLI-")]
        total = complete = 0
        for root in roots:
            tr = self.trace(root)
            if tr is not None:
                total += 1
                complete += tr.complete
        return {"total": total, "complete": complete}
