"""Parsers de log -> Event. Funciones puras, sin I/O, pensadas para probarse a fondo.

Seguridad: toda línea es entrada no confiable. Se valida longitud, formato de IDs y que
el prefijo del ID propio y el del padre correspondan a la capa declarada (un log
manipulado no puede inyectar enlaces entre capas que no son contiguas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from obsproto.common import CORE_LAYOUT, CORE_RECORD_LEN, valid_id

LAYER_ORDER = ("gateway", "bus", "core", "modern")
OWN_PREFIX = {"gateway": "GW", "bus": "BUS", "core": "CORE", "modern": "MOD"}
PARENT_PREFIX = {"gateway": "CLI", "bus": "GW", "core": "BUS", "modern": "CORE"}
MAX_LINE = 1024


@dataclass(frozen=True)
class Event:
    layer: str
    id: str
    parent_id: str
    end_ts: float  # epoch en segundos (momento de registro, al terminar la operación)
    duration_ms: int
    status: str


def _ids_consistent(layer: str, own: str, parent: str) -> bool:
    return valid_id(own, OWN_PREFIX[layer]) and valid_id(parent, PARENT_PREFIX[layer])


def parse_json_line(line: str) -> Optional[Event]:
    """Log JSON de gateway, bus y modern. Devuelve None si la línea no es válida."""
    if not line or len(line) > MAX_LINE:
        return None
    try:
        rec = json.loads(line)
        layer = rec["layer"]
        if layer not in OWN_PREFIX or layer == "core":
            return None
        own, parent = rec["id"], rec["parent_id"]
        if not _ids_consistent(layer, own, parent):
            return None
        duration = rec["duration_ms"]
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            return None
        ts = datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00"))
        return Event(layer, own, parent, ts.timestamp(), duration, str(rec.get("status", "")))
    except (ValueError, KeyError, TypeError):
        return None


def parse_core_line(line: str) -> Optional[Event]:
    """Log de ancho fijo del core (ver docs/log-formats.md)."""
    line = line.rstrip("\r\n")
    if len(line) != CORE_RECORD_LEN or not line.isascii():
        return None
    fields = {}
    pos = 0
    for name, width in CORE_LAYOUT:
        fields[name] = line[pos : pos + width]
        pos += width
    try:
        ts = datetime.strptime(fields["ts"][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        end_ts = ts.timestamp() + int(fields["ts"][14:17]) / 1000
        core_id, parent = fields["core_id"].strip(), fields["parent_id"].strip()
        if not _ids_consistent("core", core_id, parent):
            return None
        if not (fields["amount"].isdigit() and fields["status"].isdigit() and fields["dur_ms"].isdigit()):
            return None
        return Event("core", core_id, parent, end_ts, int(fields["dur_ms"]), fields["status"])
    except ValueError:
        return None
