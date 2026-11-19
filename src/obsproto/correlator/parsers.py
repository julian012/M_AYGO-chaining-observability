"""Parsers de log -> Event, uno por TIPO de fuente (ver docs/log-formats.md):

  gateway  access log de texto con pares clave=valor        parse_gateway_line
  bus      log de texto con niveles; el par está en DEBUG   BusLogParser (con estado)
  core     registro de ancho fijo, 81 caracteres            parse_core_line
  modern   spans JSON al estilo OpenTelemetry               parse_span_line

Seguridad: toda línea es entrada no confiable. Se valida longitud, formato de IDs y que
el prefijo del ID propio y el del padre correspondan a la capa declarada (un log
manipulado no puede inyectar enlaces entre capas que no son contiguas).
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from obsproto import tracelink, wire
from obsproto.common import CORE_LAYOUT, CORE_RECORD_LEN, valid_id

LAYER_ORDER = ("gateway", "bus", "core", "mft", "modern")  # mft: transferencia del archivo plano (opcional)
OWN_PREFIX = {"gateway": "GW", "bus": "BUS", "core": "CORE", "modern": "MOD"}
PARENT_PREFIX = {"gateway": "CLI", "bus": "GW", "core": "BUS", "modern": "CORE"}
MAX_LINE = 1024
HEX32 = re.compile(r"^[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class Event:
    layer: str
    id: str
    parent_id: str
    end_ts: float  # epoch en segundos (momento de registro, al terminar la operación)
    duration_ms: int
    status: str
    ext_trace_id: Optional[str] = None  # traceId OTel propio de la fuente, si lo tiene
    note: Optional[str] = None  # dato extra para mostrar (p. ej. el archivo que movió el MFT)


@dataclass(frozen=True)
class TraceRef:
    """El bus registró el tracenumber que le devolvió el core (en claro, token o máscara).

    No es un eslabón todavía: el correlacionador lo une con el registro del core que corresponda.
    """

    bus_id: str
    value: str
    ts: float


def _ids_consistent(layer: str, own: str, parent: str) -> bool:
    return valid_id(own, OWN_PREFIX[layer]) and valid_id(parent, PARENT_PREFIX[layer])


def _iso_ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------------------- #
# Gateway: access log de texto, una línea por transacción
#   2026-11-18T14:24:22.105Z INFO  gateway tid=GW-… in_id=CLI-… ch=partner proto=SOAP status=200 upstream=200 dur_ms=48
# --------------------------------------------------------------------------- #
_GW_LINE = re.compile(r"^(?P<ts>\S+) (?P<lvl>DEBUG|INFO|WARN|ERROR) +gateway (?P<kv>(?:[a-z_]+=\S+ ?)+)\Z")


def parse_gateway_line(line: str) -> Optional[Event]:
    if not line or len(line) > MAX_LINE:
        return None
    m = _GW_LINE.match(line)
    if not m:
        return None
    kv: Dict[str, str] = dict(pair.split("=", 1) for pair in m["kv"].split())
    own, parent = kv.get("tid", ""), kv.get("in_id", "")
    if not _ids_consistent("gateway", own, parent):
        return None
    if not (kv.get("dur_ms", "").isdigit() and kv.get("status", "").isdigit()):
        return None
    try:
        return Event("gateway", own, parent, _iso_ts(m["ts"]), int(kv["dur_ms"]), kv["status"])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Bus: log de texto con niveles. Tres tipos de línea por transacción:
#   INFO  … received SOAP Submit upstream=GW-…              (ID recibido, sin ID propio)
#   DEBUG … mapped msgId=BUS-… upstream=GW-… out=CORE.TRAMA   (el PAR completo: solo en DEBUG)
#   INFO  … reply msgId=BUS-… rc=00 dur_ms=44                (ID propio, resultado y duración)
# Perfil "summary" (sin DEBUG): una sola línea INFO por mensaje, con el par completo:
#   INFO  … reply msgId=BUS-… upstream=GW-… rc=00 trace=<tracenumber del core según la política> dur_ms=44
# --------------------------------------------------------------------------- #
_ID = r"[A-Z]{2,4}-[0-9a-f]{12}"
_BUS_LINE = re.compile(
    r"^(?P<ts>\S+) (?P<lvl>DEBUG|INFO|WARN|ERROR) +\[ESB\.TransactionRoute\] thread=T\d{5} "
    rf"(?:received SOAP Submit upstream=(?P<rx>{_ID})"
    rf"|mapped msgId=(?P<mid>{_ID}) upstream=(?P<mup>{_ID}) out=CORE\.TRAMA"
    rf"|reply msgId=(?P<rid>{_ID})(?: upstream=(?P<rup>{_ID}))? rc=(?P<rc>[0-9A-Z]{{2}})"
    rf"(?: trace=(?P<trace>[0-9a-z_*]{{1,20}}))? dur_ms=(?P<dur>\d{{1,7}}))\Z"
)


class BusLogParser:
    """Une la línea DEBUG (par de IDs) con la línea INFO de respuesta (estado y duración).

    Con el bus en INFO nunca llega la línea DEBUG: las respuestas quedan sin padre
    (``unlinked``) y la cadena se corta en el bus. No se intenta adivinar el par por hilo
    ni por tiempo, porque eso inventaría enlaces bajo concurrencia.
    """

    def __init__(self, max_pending: int = 100_000) -> None:
        self.max_pending = max_pending
        self._parent: OrderedDict[str, str] = OrderedDict()
        self._reply: OrderedDict[str, Tuple[float, int, str]] = OrderedDict()
        self.received = 0

    @property
    def unlinked(self) -> int:
        """Respuestas del bus cuyo ID recibido nunca apareció (p. ej. log en INFO)."""
        return len(self._reply)

    def _bound(self, d: OrderedDict) -> None:
        while len(d) > self.max_pending:
            d.popitem(last=False)

    def __call__(self, line: str) -> Optional[List[object]]:
        if not line or len(line) > MAX_LINE:
            return None
        m = _BUS_LINE.match(line)
        if not m:
            return None
        try:
            ts = _iso_ts(m["ts"])
        except ValueError:
            return None
        if m["rx"]:
            if not valid_id(m["rx"], "GW"):
                return None
            self.received += 1
            return []
        if m["mid"]:
            own, parent = m["mid"], m["mup"]
            if not _ids_consistent("bus", own, parent):
                return None
            self._parent[own] = parent
            self._bound(self._parent)
        else:
            own = m["rid"]
            if not valid_id(own, "BUS"):
                return None
            out: List[object] = []
            if m["trace"]:
                if tracelink.kind(m["trace"]) is None:
                    return None
                out.append(TraceRef(own, m["trace"], ts))
            if m["rup"]:  # perfil summary: el par viene en la misma línea INFO
                if not valid_id(m["rup"], "GW"):
                    return None
                out.append(Event("bus", own, m["rup"], ts, int(m["dur"]), m["rc"]))
                return out
            self._reply[own] = (ts, int(m["dur"]), m["rc"])
            self._bound(self._reply)
            if own in self._parent:
                end_ts, dur, rc = self._reply.pop(own)
                out.append(Event("bus", own, self._parent.pop(own), end_ts, dur, rc))
            return out
        if own in self._parent and own in self._reply:
            end_ts, dur, rc = self._reply.pop(own)
            return [Event("bus", own, self._parent.pop(own), end_ts, dur, rc)]
        return []


# --------------------------------------------------------------------------- #
# Core: registro de ancho fijo (ver docs/log-formats.md)
# --------------------------------------------------------------------------- #
def parse_core_line(line: str) -> Optional[Event]:
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
        # Core de tramas (CORE_LINK=tracenumber): no sabe quién lo llamó y el campo queda vacío.
        if not (_ids_consistent("core", core_id, parent) or (parent == "" and valid_id(core_id, "CORE"))):
            return None
        if not (fields["amount"].isdigit() and fields["status"].isdigit() and fields["dur_ms"].isdigit()):
            return None
        return Event("core", core_id, parent, end_ts, int(fields["dur_ms"]), fields["status"])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Moderno: spans JSON al estilo OpenTelemetry; el eslabón está en los atributos
# --------------------------------------------------------------------------- #
def parse_span_line(line: str) -> Optional[Event]:
    if not line or len(line) > MAX_LINE:
        return None
    try:
        span = json.loads(line)
        attrs = span["attributes"]
        own, parent = attrs["app.id"], attrs["upstream.id"]
        if not (isinstance(own, str) and isinstance(parent, str) and _ids_consistent("modern", own, parent)):
            return None
        start, end = span["startTimeUnixNano"], span["endTimeUnixNano"]
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (start, end)) or end < start:
            return None
        trace_id = span.get("traceId")
        trace_id = trace_id if isinstance(trace_id, str) and HEX32.match(trace_id) else None
        ok = span.get("status", {}).get("code") == "STATUS_CODE_OK"
        return Event("modern", own, parent, end / 1e9, (end - start) // 1_000_000, "OK" if ok else "ERROR", trace_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# MFT: log de sesión y de archivo. Una transferencia = copia + movimiento de un archivo.
#   <ts> INFO  [MFT] job=… run=RUN-… session=SSH-… transfer=MFT-… event=file.copy src=/outbox/NOV_… dst=/inbox/NOV_… …
#   <ts> INFO  [MFT] job=… run=RUN-… session=SSH-… transfer=MFT-… event=file.move src=/outbox/NOV_… status=OK
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MftTransfer:
    transfer_id: str
    file: str
    node: str
    range_start: float  # rango de horas que dice el nombre del archivo
    range_end: float
    copied_ts: float
    moved_ts: float
    session: str


_MFT_LINE = re.compile(
    r"^(?P<ts>\S+) (?P<lvl>INFO|WARN|ERROR) +\[MFT\] job=(?P<job>[A-Z0-9_]{1,40}) run=RUN-[0-9a-f]{12} "
    r"session=(?P<ses>SSH-[0-9a-f]{12})(?: transfer=(?P<tr>MFT-[0-9a-f]{12}))? event=(?P<ev>[a-z.]{4,20})"
    r"(?: (?P<rest>.*))?\Z"
)


class MftLogParser:
    """Arma una transferencia cuando ve la copia y el movimiento del mismo archivo."""

    def __init__(self, max_pending: int = 10_000) -> None:
        self._copies: OrderedDict[str, Tuple[str, float]] = OrderedDict()  # transfer -> (archivo, hora copia)
        self.max_pending = max_pending

    def __call__(self, line: str) -> Optional[List[object]]:
        if not line or len(line) > MAX_LINE:
            return None
        m = _MFT_LINE.match(line)
        if not m:
            return None
        try:
            ts = _iso_ts(m["ts"])
        except ValueError:
            return None
        kv = dict(p.split("=", 1) for p in (m["rest"] or "").split() if "=" in p)
        if m["ev"] == "file.copy" and m["tr"] and "src" in kv:
            self._copies[m["tr"]] = (kv["src"].rsplit("/", 1)[-1], ts)
            while len(self._copies) > self.max_pending:
                self._copies.popitem(last=False)
            return []
        if m["ev"] == "file.move" and m["tr"] and kv.get("status") == "OK":
            copied = self._copies.pop(m["tr"], None)
            if copied is None:
                return []
            name = copied[0]
            parsed = wire.parse_novedad_filename(name)
            if parsed is None:
                return None  # el nombre no sigue la convención: no se puede ubicar en el tiempo
            node, start, end = parsed
            return [MftTransfer(m["tr"], name, node, start, end, copied[1], ts, m["ses"])]
        return []  # ssh.connect, ssh.disconnect y otros eventos: contexto, no eslabones
