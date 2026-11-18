import pytest

from obsproto.common import HttpError
from obsproto.schema import validate_txn
from obsproto.wire import (
    TRAMA_REQ_LEN,
    TRAMA_RSP_LEN,
    parse_soap_request,
    parse_trama_request,
    parse_trama_response,
    soap_fault,
    soap_is_ok,
    soap_request,
    soap_response,
    trama_request,
    trama_response,
)

TXN = {"type": "TRF", "account": "000123456", "amount_cents": 466354}


def test_soap_roundtrip_with_ids_in_header():
    raw = soap_request(TXN, client_id="CLI-7c73f186280d", message_id="GW-e94bb7dc1205")
    txn, client_id, msg_id = parse_soap_request(raw)
    assert validate_txn(txn) == TXN
    assert (client_id, msg_id) == ("CLI-7c73f186280d", "GW-e94bb7dc1205")


def test_soap_response_and_fault():
    assert soap_is_ok(soap_response("OK"))
    assert not soap_is_ok(soap_fault("upstream_error"))
    assert not soap_is_ok(b"not xml")


@pytest.mark.parametrize(
    "raw,code",
    [
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">&e;</soapenv:Envelope>',
            "dtd_not_allowed",
        ),  # XXE
        (b"<!doctype lol><a/>", "dtd_not_allowed"),
        (b"<a><b></a>", "invalid_xml"),
        (b"<Envelope/>", "not_soap_envelope"),
        (b"", "payload_size"),
        (b"<a>" + b"x" * 5000 + b"</a>", "payload_size"),
    ],
)
def test_soap_rejects_hostile_xml(raw, code):
    with pytest.raises(HttpError) as exc:
        parse_soap_request(raw)
    assert exc.value.code == code


def test_soap_rejects_extra_fields():
    raw = soap_request(TXN).replace(b"</txn:Transaction>", b"<txn:Extra>1</txn:Extra></txn:Transaction>")
    with pytest.raises(HttpError) as exc:
        parse_soap_request(raw)
    assert exc.value.code == "invalid_fields"


def test_soap_non_numeric_amount_is_rejected_by_schema():
    raw = soap_request(TXN).replace(b"466354", b"12abc")
    txn, _, _ = parse_soap_request(raw)
    with pytest.raises(HttpError):
        validate_txn(txn)


def test_trama_request_layout_and_roundtrip():
    raw = trama_request("BUS-38523cfdf9a3", TXN)
    assert len(raw) == TRAMA_REQ_LEN == 60
    assert raw[:4] == b"0200" and raw[4:24].rstrip() == b"BUS-38523cfdf9a3"
    bus_id, txn = parse_trama_request(raw)
    assert bus_id == "BUS-38523cfdf9a3"
    assert validate_txn(txn) == TXN  # los ceros a la izquierda de la cuenta se conservan


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r[:-1],  # longitud incorrecta
        lambda r: r + b"0",
        lambda r: b"0800" + r[4:],  # tipo de mensaje desconocido
        lambda r: r[:4] + b"GW-38523cfdf9a3".ljust(20) + r[24:],  # el ID no es del bus
        lambda r: r[:48] + b"00000000ABCD",  # monto no numérico
        lambda r: r.replace(b"0", b"\xff", 1),  # no ASCII
    ],
)
def test_trama_request_rejects(mutate):
    with pytest.raises(HttpError):
        parse_trama_request(mutate(trama_request("BUS-38523cfdf9a3", TXN)))


def test_trama_response():
    raw = trama_response("BUS-38523cfdf9a3", "91")
    assert len(raw) == TRAMA_RSP_LEN == 26
    assert parse_trama_response(raw) == "91"
    assert parse_trama_response(raw[:-1]) is None
    assert parse_trama_response(b"0200" + raw[4:]) is None
