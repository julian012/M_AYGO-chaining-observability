"""Ubica la transferencia MFT que movió el archivo plano donde viajó un movimiento del core.

El MFT no abre los archivos: solo registra su nombre. El nombre dice el nodo, la fecha y el rango de horas de los
movimientos que contiene (``NOV_CORE01_20261119_072059670-072104512.txt``). Como el core arma los lotes uno tras
otro, los rangos de un nodo no se solapan: un movimiento exitoso del core cae en un solo archivo.

Regla (igual que el resto del modelo): se une solo con UN candidato. Con cero (el archivo aún no se movió) o con
varios (p. ej. rangos de dos nodos que se solapan, porque el registro del core no dice su nodo) no se adivina.
"""

from __future__ import annotations

import bisect
import threading
from typing import Dict, List, Optional, Tuple

from obsproto.correlator.parsers import Event, MftTransfer


class TransferIndex:
    def __init__(self, max_items: int = 100_000) -> None:
        self._by_start: List[Tuple[float, str]] = []  # (inicio del rango, transfer_id), ordenada
        self._items: Dict[str, MftTransfer] = {}
        self.max_items = max_items
        self.ambiguous = 0
        self._lock = threading.Lock()

    def add(self, t: MftTransfer) -> None:
        with self._lock:
            if t.transfer_id in self._items:
                return
            self._items[t.transfer_id] = t
            bisect.insort(self._by_start, (t.range_start, t.transfer_id))
            if len(self._by_start) > self.max_items * 1.1:
                for _, tid in self._by_start[: len(self._by_start) - self.max_items]:
                    self._items.pop(tid, None)
                del self._by_start[: len(self._by_start) - self.max_items]

    def __len__(self) -> int:
        return len(self._items)

    def find(self, ts: float) -> Optional[MftTransfer]:
        """La transferencia cuyo rango contiene ``ts``, si es única."""
        with self._lock:
            hi = bisect.bisect_right(self._by_start, (ts, "~"))
            cands = []
            for _start, tid in reversed(self._by_start[max(0, hi - 50) : hi]):  # rangos que empiezan antes de ts
                t = self._items[tid]
                if t.range_start <= ts <= t.range_end + 0.0005:  # el nombre tiene resolución de ms
                    cands.append(t)
            if len(cands) > 1:
                self.ambiguous += 1
            return cands[0] if len(cands) == 1 else None

    def side_event(self, core: Event) -> Optional[Event]:
        """Evento "mft" para la traza de un movimiento del core (lo usa IdGraph.trace)."""
        t = self.find(core.end_ts)
        if t is None:
            return None
        return Event(
            "mft",
            t.transfer_id,
            core.id,
            t.moved_ts,
            max(0, int((t.moved_ts - t.copied_ts) * 1000)),
            "OK",
            note=t.file,
        )
