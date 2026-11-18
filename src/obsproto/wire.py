"""Formatos de mensaje entre capas (lo que viaja por la red, no lo que se registra en el log).

Cada salto usa el protocolo que tendría en una integración empresarial típica, y en cada uno
el ID de la capa anterior viaja en un lugar distinto:

  canal móvil/web  -> gateway : REST/JSON      ID del cliente en el header ``X-Client-Id``
  canal de tercero -> gateway : SOAP 1.1/XML   ID del cliente en ``soapenv:Header/txn:ClientRequestId``
  gateway          -> bus     : SOAP 1.1/XML   ID del gateway en ``soapenv:Header/wsa:MessageID``
  bus              -> core    : trama de ancho fijo (estilo copybook); ID del bus en las posiciones 4-23
  core             -> moderno : REST/JSON      ID del core en el header ``X-Upstream-Id``

Seguridad del XML: el cuerpo es entrada no confiable. Se limita el tamaño, se rechaza cualquier
``<!DOCTYPE`` o ``<!ENTITY`` (evita XXE y expansión de entidades) y se exige la estructura exacta.
"""

from __future__ import annotations

import re
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
    ("bus_id", 20),  # eco del ID recibido
    ("rc", 2),  # 00 = OK, 91 = fallo
)
TRAMA_REQ_LEN = sum(w for _, w in TRAMA_REQ_LAYOUT)
TRAMA_RSP_LEN = sum(w for _, w in TRAMA_RSP_LAYOUT)


def _cut(record: str, layout) -> Dict[str, str]:
    out, pos = {}, 0
    for name, width in layout:
        out[name] = record[pos : pos + width]
        pos += width
    return out


def trama_request(bus_id: str, txn: Dict[str, object]) -> bytes:
    record = (
        "0200"
        + bus_id.ljust(20)
        + str(txn["type"]).ljust(4)
        + str(txn["account"]).ljust(20)
        + f"{int(txn['amount_cents']):012d}"  # type: ignore[call-overload]
    )
    if len(record) != TRAMA_REQ_LEN:
        raise ValueError("trama con longitud inválida")
    return record.encode("ascii")


def parse_trama_request(raw: bytes) -> Tuple[str, Dict[str, object]]:
    """Devuelve (ID del bus, transacción sin validar). Rechaza cualquier desviación del layout."""
    try:
        record = raw.decode("ascii")
    except UnicodeDecodeError:
        raise HttpError(400, "invalid_trama") from None
    if len(record) != TRAMA_REQ_LEN:
        raise HttpError(400, "invalid_trama")
    f = _cut(record, TRAMA_REQ_LAYOUT)
    bus_id = f["bus_id"].strip()
    if f["mti"] != "0200" or not valid_id(bus_id, "BUS"):
        raise HttpError(400, "invalid_trama")
    account = f["account"].rstrip(" ")
    if not (account.isdigit() and f["amount"].isdigit()):
        raise HttpError(400, "invalid_trama")
    return bus_id, {"type": f["txn"].strip(), "account": account, "amount_cents": int(f["amount"])}


def trama_response(bus_id: str, rc: str) -> bytes:
    return ("0210" + bus_id.ljust(20) + rc.rjust(2, "0")[-2:]).encode("ascii")


def parse_trama_response(raw: bytes) -> Optional[str]:
    """Devuelve el código de respuesta (rc) o None si la trama no es válida."""
    try:
        record = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(record) != TRAMA_RSP_LEN:
        return None
    f = _cut(record, TRAMA_RSP_LAYOUT)
    return f["rc"] if f["mti"] == "0210" and f["rc"].isdigit() else None
