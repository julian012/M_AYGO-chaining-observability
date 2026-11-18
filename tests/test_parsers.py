from datetime import datetime, timezone

import pytest

from obsproto.common import CORE_RECORD_LEN, format_core_record
from obsproto.correlator.parsers import parse_core_line, parse_json_line

TS = datetime(2026, 11, 12, 15, 30, 45, 123000, tzinfo=timezone.utc)


def core_line(**over):
    args = dict(
        core_id="CORE-aaaaaaaaaaaa", parent_id="BUS-bbbbbbbbbbbb", txn="TRF", amount_cents=2500, status="00", dur_ms=42
    )
    args.update(over)
    return format_core_record(
        TS, args["core_id"], args["parent_id"], args["txn"], args["amount_cents"], args["status"], args["dur_ms"]
    )


def jline(**over):
    rec = {
        "ts": "2026-11-12T15:30:45.123Z",
        "layer": "bus",
        "id": "BUS-bbbbbbbbbbbb",
        "parent_id": "GW-cccccccccccc",
        "status": 200,
        "duration_ms": 12,
    }
    rec.update(over)
    import json

    return json.dumps(rec)


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


def test_json_ok():
    ev = parse_json_line(jline())
    assert ev is not None and ev.layer == "bus" and ev.parent_id == "GW-cccccccccccc" and ev.duration_ms == 12


@pytest.mark.parametrize(
    "over",
    [
        {"layer": "core"},  # core no se parsea como JSON
        {"layer": "unknown"},
        {"id": "GW-bbbbbbbbbbbb"},  # prefijo no corresponde a la capa
        {"parent_id": "CLI-cccccccccccc"},  # padre de bus debe ser GW
        {"id": "BUS-XYZ"},
        {"duration_ms": -1},
        {"duration_ms": True},
        {"duration_ms": "12"},
        {"ts": "no-es-fecha"},
    ],
)
def test_json_rejects_invalid(over):
    assert parse_json_line(jline(**over)) is None


def test_json_rejects_garbage_and_oversize():
    assert parse_json_line("not json") is None
    assert parse_json_line("[1,2,3]") is None
    assert parse_json_line("{" + " " * 2000 + "}") is None
    assert parse_json_line("") is None


def test_log_injection_is_just_data():
    # un valor con salto de línea no puede fabricar una segunda entrada válida
    line = jline(status="200\n" + jline(id="BUS-dddddddddddd"))
    assert "\n" not in line
    ev = parse_json_line(line)
    assert ev is not None and ev.id == "BUS-bbbbbbbbbbbb"
