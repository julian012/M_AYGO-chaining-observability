import json
from datetime import datetime, timezone

import pytest

from obsproto.common import CORE_RECORD_LEN, format_core_record
from obsproto.correlator.parsers import BusLogParser, parse_core_line, parse_gateway_line, parse_span_line

TS = datetime(2026, 11, 12, 15, 30, 45, 123000, tzinfo=timezone.utc)


def core_line(**over):
    args = dict(
        core_id="CORE-aaaaaaaaaaaa", parent_id="BUS-bbbbbbbbbbbb", txn="TRF", amount_cents=2500, status="00", dur_ms=42
    )
    args.update(over)
    return format_core_record(
        TS, args["core_id"], args["parent_id"], args["txn"], args["amount_cents"], args["status"], args["dur_ms"]
    )


def test_core_record_has_fixed_width():
    assert len(core_line()) == CORE_RECORD_LEN == 81


def test_core_roundtrip():
    ev = parse_core_line(core_line())
    assert ev is not None
    assert (ev.layer, ev.id, ev.parent_id, ev.duration_ms, ev.status) == (
        "core",
        "CORE-aaaaaaaaaaaa",
        "BUS-bbbbbbbbbbbb",
        42,
        "00",
    )
    assert ev.end_ts == pytest.approx(TS.timestamp())


def test_core_rejects_wrong_length_and_noise():
    assert parse_core_line(core_line()[:-1]) is None
    assert parse_core_line(core_line() + "X") is None
    assert parse_core_line("") is None


def test_core_rejects_non_numeric_fields():
    line = core_line()
    assert parse_core_line(line[:57] + "TRF " + "ABCDEFGHIJKL" + line[73:]) is None


def test_core_rejects_wrong_parent_layer():
    # el padre de core debe ser BUS-, no GW-
    assert parse_core_line(core_line(parent_id="GW-cccccccccccc")) is None


# --------------------------------------------------------------------------- #
# Gateway: access log clave=valor
# --------------------------------------------------------------------------- #
GW_LINE = (
    "2026-11-18T14:24:22.105Z INFO  gateway tid=GW-e94bb7dc1205 in_id=CLI-7c73f186280d "
    "ch=partner proto=SOAP status=200 upstream=200 dur_ms=48"
)


def test_gateway_ok():
    ev = parse_gateway_line(GW_LINE)
    assert ev is not None
    assert (ev.layer, ev.id, ev.parent_id, ev.duration_ms, ev.status) == (
        "gateway",
        "GW-e94bb7dc1205",
        "CLI-7c73f186280d",
        48,
        "200",
    )


@pytest.mark.parametrize(
    "bad",
    [
        GW_LINE.replace("tid=GW-", "tid=BUS-"),  # prefijo propio de otra capa
        GW_LINE.replace("in_id=CLI-", "in_id=GW-"),  # padre que no es el cliente
        GW_LINE.replace("dur_ms=48", "dur_ms=-1"),
        GW_LINE.replace("dur_ms=48", ""),  # falta un campo obligatorio
        GW_LINE.replace(" gateway ", " bus "),
        GW_LINE.replace("2026-11-18T14:24:22.105Z", "ayer"),
        GW_LINE + " x",  # token sin clave=valor
        "",
        GW_LINE + " pad=" + "x" * 1100,
    ],
)
def test_gateway_rejects(bad):
    assert parse_gateway_line(bad) is None


# --------------------------------------------------------------------------- #
# Bus: log con niveles; el par solo está en DEBUG
# --------------------------------------------------------------------------- #
FLOW = "[ESB.TransactionRoute] thread=T01234"
RX = f"2026-11-18T14:24:22.061Z INFO  {FLOW} received SOAP Submit upstream=GW-e94bb7dc1205"
MAP = f"2026-11-18T14:24:22.062Z DEBUG {FLOW} mapped msgId=BUS-38523cfdf9a3 upstream=GW-e94bb7dc1205 out=CORE.TRAMA"
RPL = f"2026-11-18T14:24:22.104Z INFO  {FLOW} reply msgId=BUS-38523cfdf9a3 rc=00 dur_ms=44"


def test_bus_debug_joins_mapping_and_reply():
    p = BusLogParser()
    assert p(RX) == [] and p(MAP) == []
    (ev,) = p(RPL)
    assert (ev.layer, ev.id, ev.parent_id, ev.duration_ms, ev.status) == (
        "bus",
        "BUS-38523cfdf9a3",
        "GW-e94bb7dc1205",
        44,
        "00",
    )
    assert p.unlinked == 0 and p.received == 1


def test_bus_reply_before_mapping_still_joins():
    p = BusLogParser()
    assert p(RPL) == []
    assert len(p(MAP)) == 1


def test_bus_info_only_breaks_the_chain():
    # sin la línea DEBUG nada une el ID recibido con el propio: no se inventa el enlace
    p = BusLogParser()
    assert p(RX) == [] and p(RPL) == []
    assert p.unlinked == 1


def test_bus_pending_is_bounded():
    p = BusLogParser(max_pending=2)
    for i in range(5):
        p(RPL.replace("38523cfdf9a3", f"{i:012x}"))
    assert p.unlinked == 2


@pytest.mark.parametrize(
    "bad",
    [
        MAP.replace("upstream=GW-", "upstream=CLI-"),  # el padre del bus debe ser GW
        MAP.replace("msgId=BUS-", "msgId=CORE-"),
        RPL.replace("dur_ms=44", "dur_ms=abc"),
        RPL.replace("[ESB.TransactionRoute]", "[Otro.Flujo]"),
        RPL + " extra",
        "texto libre",
        "",
    ],
)
def test_bus_rejects(bad):
    assert BusLogParser()(bad) is None


# --------------------------------------------------------------------------- #
# Moderno: span OTel
# --------------------------------------------------------------------------- #
def span(**over):
    rec = {
        "resource": {"service.name": "modern-notifications"},
        "traceId": "9f2c" + "0" * 28,
        "spanId": "a41b" + "0" * 12,
        "parentSpanId": "",
        "name": "POST /notify",
        "startTimeUnixNano": 1_763_475_862_000_000_000,
        "endTimeUnixNano": 1_763_475_862_003_000_000,
        "status": {"code": "STATUS_CODE_OK"},
        "attributes": {"app.id": "MOD-e03081df0fbb", "upstream.id": "CORE-4510801cd951"},
    }
    rec.update(over)
    return json.dumps(rec)


def test_span_ok_keeps_otel_trace_id():
    ev = parse_span_line(span())
    assert ev is not None
    assert (ev.layer, ev.id, ev.parent_id, ev.duration_ms, ev.status) == (
        "modern",
        "MOD-e03081df0fbb",
        "CORE-4510801cd951",
        3,
        "OK",
    )
    assert ev.ext_trace_id == "9f2c" + "0" * 28


@pytest.mark.parametrize(
    "over",
    [
        {"attributes": {"app.id": "MOD-e03081df0fbb"}},  # falta el eslabón
        {"attributes": {"app.id": "MOD-e03081df0fbb", "upstream.id": "BUS-4510801cd951"}},
        {"attributes": {"app.id": 7, "upstream.id": "CORE-4510801cd951"}},
        {"endTimeUnixNano": 1},  # termina antes de empezar
        {"startTimeUnixNano": True},
        {"attributes": "x"},
    ],
)
def test_span_rejects(over):
    assert parse_span_line(span(**over)) is None


def test_span_rejects_garbage_and_oversize():
    assert parse_span_line("not json") is None
    assert parse_span_line("[1,2,3]") is None
    assert parse_span_line("{" + " " * 2000 + "}") is None


def test_span_bad_trace_id_is_dropped_not_fatal():
    ev = parse_span_line(span(traceId="no-hex"))
    assert ev is not None and ev.ext_trace_id is None
