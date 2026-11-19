"""Formatos de mensaje entre capas (lo que viaja por la red, no lo que se registra en el log).

Cada salto usa el protocolo que tendría en una integración empresarial típica, y en cada uno
el ID de la capa anterior viaja en un lugar distinto:

  canal móvil/web  -> gateway : REST/JSON      ID del cliente en el header ``X-Client-Id``
  canal de tercero -> gateway : SOAP 1.1/XML   ID del cliente en ``soapenv:Header/txn:ClientRequestId``
  gateway          -> bus     : SOAP 1.1/XML   ID del gateway en ``soapenv:Header/wsa:MessageID``
  bus              -> core    : trama de ancho fijo (estilo copybook) por un SOCKET TCP, cada mensaje con una
                                cabecera de 4 dígitos ASCII con su longitud (no es HTTP);
                                ID del bus en las posiciones 4-23,
                                o vacías con CORE_LINK=tracenumber: el core (tipo Stratus) no recibe el ID del bus,
                                asigna su propio consecutivo (tracenumber) y lo devuelve en la respuesta
  core             -> moderno : ARCHIVO PLANO de novedades (ancho fijo) transferido por FTP en lotes;
                                el ID del core (tracenumber) va en cada registro. El moderno lo procesa
                                después: el tramo es asíncrono

Seguridad del XML: el cuerpo es entrada no confiable. Se limita el tamaño, se rechaza cualquier
``<!DOCTYPE`` o ``<!ENTITY`` (evita XXE y expansión de entidades) y se exige la estructura exacta.
"""

from __future__ import annotations

import re
import socket
import xml.etree.ElementTree as ET  # noqa: S405 - se rechazan DTD/entidades antes de parsear
from typing import Dict, Optional, Tuple

from obsproto.common import MAX_BODY_BYTES, HttpError, valid_id

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSA_NS = "http://www.w3.org/2005/08/addressing"
TXN_NS = "urn:obsproto:txn:v1"
SOAP_CONTENT_TYPE = "text/xml; charset=utf-8"
TRAMA_CONTENT_TYPE = "application/octet-stream"

_NS = {"soapenv": SOAP_NS, "wsa": WSA_NS, "txn": TXN_NS}
_FORBIDDEN_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_DIGITS = re.compile(r"^[0-9]{1,12}\Z")


# --------------------------------------------------------------------------- #
# SOAP
# --------------------------------------------------------------------------- #
def _envelope(header: str, body: str) -> bytes:
    return (
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_NS}" xmlns:wsa="{WSA_NS}" xmlns:txn="{TXN_NS}">'
        f"<soapenv:Header>{header}</soapenv:Header><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
    ).encode()


def _txn_xml(txn: Dict[str, object]) -> str:
    # Los valores ya pasaron por validate_txn (lista blanca): no contienen caracteres XML especiales.
    return (
        f"<txn:Transaction><txn:Type>{txn['type']}</txn:Type><txn:Account>{txn['account']}</txn:Account>"
        f"<txn:AmountCents>{txn['amount_cents']}</txn:AmountCents></txn:Transaction>"
    )


def soap_request(txn: Dict[str, object], *, client_id: Optional[str] = None, message_id: Optional[str] = None) -> bytes:
    header = ""
    if client_id:
        header += f"<txn:ClientRequestId>{client_id}</txn:ClientRequestId>"
    if message_id:
        header += f"<wsa:MessageID>{message_id}</wsa:MessageID>"
    return _envelope(header, _txn_xml(txn))


def soap_response(result: str = "OK", client_id: Optional[str] = None) -> bytes:
    header = f"<txn:ClientRequestId>{client_id}</txn:ClientRequestId>" if client_id else ""
    return _envelope(header, f"<txn:TransactionResponse><txn:Result>{result}</txn:Result></txn:TransactionResponse>")


def soap_fault(code: str, actor: str = "Server") -> bytes:
    return _envelope(
        "", f"<soapenv:Fault><faultcode>soapenv:{actor}</faultcode><faultstring>{code}</faultstring></soapenv:Fault>"
    )


def _parse_xml(raw: bytes) -> ET.Element:
    if not raw or len(raw) > MAX_BODY_BYTES:
        raise HttpError(413, "payload_size")
    if _FORBIDDEN_XML.search(raw):
        raise HttpError(400, "dtd_not_allowed")
    try:
        root = ET.fromstring(raw)  # noqa: S314 - DTD y entidades ya rechazadas
    except ET.ParseError:
        raise HttpError(400, "invalid_xml") from None
    if root.tag != f"{{{SOAP_NS}}}Envelope":
        raise HttpError(400, "not_soap_envelope")
    return root


def _text(root: ET.Element, path: str) -> Optional[str]:
    el = root.find(path, _NS)
    return None if el is None or el.text is None else el.text.strip()


def parse_soap_request(raw: bytes) -> Tuple[Dict[str, object], Optional[str], Optional[str]]:
    """Devuelve (transacción sin validar, ClientRequestId, wsa:MessageID)."""
    root = _parse_xml(raw)
    body = root.find("soapenv:Body/txn:Transaction", _NS)
    if body is None:
        raise HttpError(422, "invalid_fields")
    fields = {child.tag.split("}")[-1] for child in body}
    if fields != {"Type", "Account", "AmountCents"}:
        raise HttpError(422, "invalid_fields")
    amount = _text(body, "txn:AmountCents") or ""
    txn: Dict[str, object] = {
        "type": _text(body, "txn:Type"),
        "account": _text(body, "txn:Account"),
        "amount_cents": int(amount) if _DIGITS.match(amount) else amount,  # validate_txn rechaza no-enteros
    }
    return txn, _text(root, "soapenv:Header/txn:ClientRequestId"), _text(root, "soapenv:Header/wsa:MessageID")


def soap_is_ok(raw: bytes) -> bool:
    try:
        root = _parse_xml(raw)
    except HttpError:
        return False
    return _text(root, "soapenv:Body/txn:TransactionResponse/txn:Result") == "OK"


# --------------------------------------------------------------------------- #
# Tramas de ancho fijo bus <-> core (la interfaz que expone un core COBOL)
# --------------------------------------------------------------------------- #
TRAMA_REQ_LAYOUT = (
    ("mti", 4),  # 0200 = solicitud
    ("bus_id", 20),  # ID del bus, alineado a la izquierda
    ("txn", 4),  # TRF | PAY | BAL
    ("account", 20),  # PIC X(20): alineado a la izquierda, relleno con espacios
    ("amount", 12),  # centavos, ceros a la izquierda
)
TRAMA_RSP_LAYOUT = (
    ("mti", 4),  # 0210 = respuesta
    ("bus_id", 20),  # eco del ID recibido (vacío si no se envió)
    ("rc", 2),  # 00 = OK, 91 = fallo
    ("tracenumber", 12),  # consecutivo del movimiento en el core; ceros si no aplica
)
TRAMA_REQ_LEN = sum(w for _, w in TRAMA_REQ_LAYOUT)
TRAMA_RSP_LEN = sum(w for _, w in TRAMA_RSP_LAYOUT)


def _cut(record: str, layout) -> Dict[str, str]:
    out, pos = {}, 0
    for name, width in layout:
        out[name] = record[pos : pos + width]
        pos += width
    return out


def trama_request(bus_id: str, txn: Dict[str, object], include_id: bool = True) -> bytes:
    record = (
        "0200"
        + (bus_id if include_id else "").ljust(20)
        + str(txn["type"]).ljust(4)
        + str(txn["account"]).ljust(20)
        + f"{int(txn['amount_cents']):012d}"  # type: ignore[call-overload]
    )
    if len(record) != TRAMA_REQ_LEN:
        raise ValueError("trama con longitud inválida")
    return record.encode("ascii")


def parse_trama_request(raw: bytes, allow_blank_id: bool = False) -> Tuple[Optional[str], Dict[str, object]]:
    """Devuelve (ID del bus o None, transacción sin validar). Rechaza cualquier desviación del layout."""
    try:
        record = raw.decode("ascii")
    except UnicodeDecodeError:
        raise HttpError(400, "invalid_trama") from None
    if len(record) != TRAMA_REQ_LEN:
        raise HttpError(400, "invalid_trama")
    f = _cut(record, TRAMA_REQ_LAYOUT)
    bus_id = f["bus_id"].strip()
    if f["mti"] != "0200" or not (valid_id(bus_id, "BUS") or (allow_blank_id and bus_id == "")):
        raise HttpError(400, "invalid_trama")
    account = f["account"].rstrip(" ")
    if not (account.isdigit() and f["amount"].isdigit()):
        raise HttpError(400, "invalid_trama")
    return bus_id or None, {"type": f["txn"].strip(), "account": account, "amount_cents": int(f["amount"])}


def trama_response(bus_id: Optional[str], rc: str, tracenumber: int = 0) -> bytes:
    return ("0210" + (bus_id or "").ljust(20) + rc.rjust(2, "0")[-2:] + f"{tracenumber:012d}"[-12:]).encode("ascii")


def parse_trama_response(raw: bytes) -> Optional[Tuple[str, Optional[str]]]:
    """Devuelve (rc, tracenumber o None) o None si la trama no es válida."""
    try:
        record = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(record) != TRAMA_RSP_LEN:
        return None
    f = _cut(record, TRAMA_RSP_LAYOUT)
    if f["mti"] != "0210" or not f["rc"].isdigit() or not f["tracenumber"].isdigit():
        return None
    return f["rc"], (f["tracenumber"] if int(f["tracenumber"]) else None)


# --------------------------------------------------------------------------- #
# Transporte de tramas por TCP: cabecera de 4 dígitos ASCII con la longitud del mensaje
# --------------------------------------------------------------------------- #
TCP_HEADER = 4
MAX_FRAME = 1024


def tcp_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME:
        raise ValueError("trama demasiado larga")
    return f"{len(payload):04d}".encode("ascii") + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("conexión cerrada a mitad de la trama")
        buf += chunk
    return buf


def tcp_read_frame(sock: socket.socket) -> Optional[bytes]:
    """Lee un mensaje. None si el otro lado cerró limpio antes de empezar uno nuevo."""
    first = sock.recv(1)
    if not first:
        return None
    head = first + _recv_exact(sock, TCP_HEADER - 1)
    if not head.isdigit() or not 0 < int(head) <= MAX_FRAME:
        raise ValueError("cabecera de longitud inválida")
    return _recv_exact(sock, int(head))


def tcp_exchange(host: str, port: int, payload: bytes, timeout: float = 3.0) -> Optional[bytes]:
    """Envía una trama y espera la respuesta. None si el core no responde (caída o timeout)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(tcp_frame(payload))
            return tcp_read_frame(sock)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Archivo plano de novedades core -> moderno (ancho fijo, 63 caracteres por registro)
#   00 cabecera: fecha-hora de generación y nodo
#   01 detalle:  ID del core (tracenumber), transacción, monto, estado, hora del movimiento
#   99 cola:     cantidad de registros de detalle (control de integridad de la transferencia)
# --------------------------------------------------------------------------- #
NOVEDAD_LEN = 63
NOVEDAD_DETAIL = (
    ("tipo", 2),  # 01
    ("core_id", 20),  # CORE-<…> alineado a la izquierda
    ("txn", 4),
    ("amount", 12),
    ("status", 2),
    ("ts", 17),  # YYYYMMDDHHMMSSmmm del movimiento en el core
    ("filler", 6),
)


def novedad_header(stamp: str, node: str) -> str:
    return ("00" + stamp[:14] + node.ljust(8)[:8]).ljust(NOVEDAD_LEN)


def novedad_detail(core_id: str, txn: str, amount_cents: int, status: str, ts: str) -> str:
    rec = "01" + core_id.ljust(20) + txn.ljust(4)[:4] + f"{amount_cents:012d}"[-12:] + status[:2] + ts[:17]
    return rec.ljust(NOVEDAD_LEN)


def novedad_trailer(count: int) -> str:
    return ("99" + f"{count:08d}").ljust(NOVEDAD_LEN)


def parse_novedades(text: str) -> Optional[list]:
    """Valida cabecera, detalle y cola. Devuelve la lista de detalles (dict) o None si el archivo no es íntegro."""
    lines = [ln for ln in text.splitlines() if ln]
    if len(lines) < 2 or any(len(ln) != NOVEDAD_LEN or not ln.isascii() for ln in lines):
        return None
    if not (lines[0].startswith("00") and lines[-1].startswith("99")):
        return None
    details = []
    for ln in lines[1:-1]:
        if not ln.startswith("01"):
            return None
        f = _cut(ln, NOVEDAD_DETAIL)
        core_id = f["core_id"].strip()
        if not valid_id(core_id, "CORE") or not f["amount"].isdigit() or not f["ts"].isdigit():
            return None
        details.append(
            {
                "core_id": core_id,
                "txn": f["txn"].strip(),
                "amount": int(f["amount"]),
                "status": f["status"],
                "ts": f["ts"],
            }
        )
    count = lines[-1][2:10]
    if not count.isdigit() or int(count) != len(details):
        return None  # transferencia incompleta: no se procesa
    return details
