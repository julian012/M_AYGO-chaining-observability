"""Unión bus -> core cuando el core NO recibe el ID del bus (tipo Stratus, tracenumber)."""

import pytest

from obsproto import tracelink
from obsproto.common import format_core_record
from obsproto.correlator.corelink import CoreLinker
from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import BusLogParser, Event, TraceRef, parse_core_line

KEY = "k" * 64
FLOW = "[ESB.TransactionRoute] thread=T01234"


def core_ev(tn: int, t: float) -> Event:
    return Event("core", f"CORE-{tn:012d}", "", t, 30, "00")


def chain_upto_bus(i: int, t: float):
    s = f"{i:012x}"
    return [
        Event("gateway", f"GW-{s}", f"CLI-{s}", t + 0.02, 40, "200"),
        Event("bus", f"BUS-{s}", f"GW-{s}", t + 0.01, 35, "00"),
    ]


def setup(n=5, t0=1000.0, gap=0.01):
    g = IdGraph()
    for i in range(1, n + 1):
        for ev in chain_upto_bus(i, t0 + i * gap):
            g.add(ev)
        g.add(core_ev(500000 + i, t0 + i * gap))
    return g


# ------------------------------------------------------------------ política de ofuscación
def test_obfuscation_policies():
    tn = "000123456789"
    assert tracelink.obfuscate(tn, "clear") == tn
    tok = tracelink.obfuscate(tn, "token", KEY)
    assert tok == tracelink.token(tn, KEY) and tok.startswith("tok_") and tn not in tok
    assert tracelink.token(tn, KEY) != tracelink.token(tn, "otra-llave")  # sin la llave no se puede unir
    assert tracelink.obfuscate(tn, "mask", visible=4) == "********6789"
    assert tracelink.obfuscate(tn, "none") is None
    assert [tracelink.kind(v) for v in (tn, tok, "********6789", "xx", "*" * 12)] == [
        "clear",
        "token",
        "mask",
        None,
        None,
    ]


# ------------------------------------------------------------------ parsers
def test_core_record_without_parent_is_accepted():
    from datetime import datetime, timezone

    line = format_core_record(
        datetime(2026, 11, 19, tzinfo=timezone.utc), "CORE-000000500001", "", "PAY", 100, "00", 30
    )
    ev = parse_core_line(line)
    assert ev is not None and ev.id == "CORE-000000500001" and ev.parent_id == ""


def test_bus_summary_line_gives_event_and_trace_ref():
    line = (
        f"2026-11-19T05:34:23.099Z INFO  {FLOW} reply msgId=BUS-fc2828571fcf upstream=GW-3ce4da52d9d9 "
        "rc=00 trace=tok_9f2c41ab00112233 dur_ms=61"
    )
    out = BusLogParser()(line)
    ref = next(x for x in out if isinstance(x, TraceRef))
    ev = next(x for x in out if isinstance(x, Event))
    assert (ref.bus_id, ref.value) == ("BUS-fc2828571fcf", "tok_9f2c41ab00112233")
    assert (ev.id, ev.parent_id, ev.status) == ("BUS-fc2828571fcf", "GW-3ce4da52d9d9", "00")


@pytest.mark.parametrize("bad", ["trace=abc", "trace=tok_XYZ", "trace=************"])
def test_bus_rejects_invalid_trace(bad):
    line = f"2026-11-19T05:34:23.099Z INFO  {FLOW} reply msgId=BUS-fc2828571fcf upstream=GW-3ce4da52d9d9 rc=00 {bad} dur_ms=61"
    assert BusLogParser()(line) is None


# ------------------------------------------------------------------ grafo
def test_orphan_core_records_do_not_merge_into_one_trace():
    g = setup()
    tr = g.trace("CORE-000000500001")
    assert tr is not None and [e.id for e in tr.events] == ["CORE-000000500001"]  # no arrastra a los demás huérfanos
    assert not g.trace("CLI-000000000001").complete


# ------------------------------------------------------------------ unión
@pytest.mark.parametrize("policy", ["clear", "token"])
def test_deterministic_link(policy):
    g = setup()
    lk = CoreLinker(g, key=KEY)
    for i in range(1, 6):
        lk.on_core_event(g._events[f"CORE-{500000 + i:012d}"])
        value = tracelink.obfuscate(f"{500000 + i:012d}", policy, KEY)
        lk.on_ref(TraceRef(f"BUS-{i:012x}", value, 1000.0 + i * 0.01))
    lk.resolve()
    assert lk.stats()["linked"] == 5
    tr = g.trace("CLI-000000000003")
    assert [e.layer for e in tr.events] == ["gateway", "bus", "core"]
    assert next(e for e in tr.events if e.layer == "core").id == "CORE-000000500003"


def test_ref_before_core_record_waits_then_links():
    g = setup(n=1)
    lk = CoreLinker(g)
    lk.on_ref(TraceRef("BUS-000000000001", "000000500001", 1000.01))
    lk.resolve()
    assert lk.stats()["pending"] == 1  # el log del core todavía no se ha leído
    lk.on_core_event(g._events["CORE-000000500001"])
    lk.resolve()
    assert lk.stats()["linked"] == 1 and lk.stats()["pending"] == 0


def test_mask_links_only_with_a_single_candidate():
    g = setup(n=3)
    lk = CoreLinker(g, window_s=1.0)
    for i in range(1, 4):
        lk.on_core_event(g._events[f"CORE-{500000 + i:012d}"])
    g.add(core_ev(999, 1005.0))  # un registro posterior a la ventana: el log del core ya la pasó
    lk.on_core_event(g._events[f"CORE-{999:012d}"])
    lk.on_ref(TraceRef("BUS-000000000002", "********0002", 1000.02))
    lk.resolve()
    assert lk.stats()["linked"] == 1


def test_mask_with_two_candidates_is_ambiguous_and_not_linked():
    g = IdGraph()
    for ev in chain_upto_bus(1, 1000.0):
        g.add(ev)
    for tn, t in ((500001, 1000.0), (510001, 1000.2), (999, 1010.0)):  # mismos 4 últimos dígitos en la ventana
        g.add(core_ev(tn, t))
    lk = CoreLinker(g, window_s=1.0)
    for cid in ("CORE-000000500001", "CORE-000000510001", "CORE-000000000999"):
        lk.on_core_event(g._events[cid])
    lk.on_ref(TraceRef("BUS-000000000001", "********0001", 1000.01))
    lk.resolve()
    assert lk.stats()["ambiguous"] == 1 and lk.stats()["linked"] == 0
    assert not g.trace("CLI-000000000001").complete  # nunca se adivina


def test_mask_waits_until_core_log_passes_the_window():
    g = setup(n=1)
    lk = CoreLinker(g, window_s=1.0)
    lk.on_core_event(g._events["CORE-000000500001"])
    lk.on_ref(TraceRef("BUS-000000000001", "********0001", 1000.01))
    lk.resolve()
    assert lk.stats()["pending"] == 1  # aún puede llegar otro candidato con la misma terminación


def test_unmatched_ref_expires():
    g = setup(n=1)
    lk = CoreLinker(g, window_s=1.0)
    lk.on_ref(TraceRef("BUS-000000000001", "000000777777", 1000.0))
    g.add(core_ev(888, 1100.0))
    lk.on_core_event(g._events[f"CORE-{888:012d}"])
    lk.resolve()
    assert lk.stats()["unmatched"] == 1 and lk.stats()["pending"] == 0


def test_none_policy_leaves_chain_broken():
    g = setup(n=1)
    lk = CoreLinker(g)
    lk.on_core_event(g._events["CORE-000000500001"])
    lk.resolve()  # el bus no registró nada: no hay referencia que unir
    assert lk.stats()["refs"] == 0
    assert g.trace("CLI-000000000001").missing_layers == ["core", "modern"]


def test_mask_resolves_by_clock_when_core_log_is_caught_up():
    # Sin tráfico nuevo, el log del core nunca pasa la ventana; si ya se leyó completo, decide el reloj.
    g = setup(n=1)
    lk = CoreLinker(g, window_s=1.0)
    lk.on_core_event(g._events["CORE-000000500001"])
    lk.on_ref(TraceRef("BUS-000000000001", "********0001", 1000.01))
    lk.resolve(now=1003.0, core_caught_up=False)
    assert lk.stats()["pending"] == 1  # releyendo un log viejo: el reloj no cuenta
    lk.resolve(now=1003.0, core_caught_up=True)
    assert lk.stats()["linked"] == 1
