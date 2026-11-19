import itertools

from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import Event

CLI, GW, BUS, CORE, MOD = (
    "CLI-000000000001",
    "GW-000000000001",
    "BUS-000000000001",
    "CORE-000000000001",
    "MOD-000000000001",
)


def chain(suffix="1", t=1000.0):
    s = suffix.rjust(12, "0")
    cli, gw, bus, core, mod = (f"{p}-{s}" for p in ("CLI", "GW", "BUS", "CORE", "MOD"))
    return [
        Event("modern", mod, core, t + 0.05, 2, "200"),
        Event("core", core, bus, t + 0.06, 40, "00"),
        Event("bus", bus, gw, t + 0.07, 50, "200"),
        Event("gateway", gw, cli, t + 0.08, 60, "200"),
    ]


def test_full_trace_from_any_id():
    g = IdGraph()
    for ev in chain():
        g.add(ev)
    for start in (CLI, GW, BUS, CORE, MOD):
        tr = g.trace(start)
        assert tr is not None and tr.complete, start
        assert [e.layer for e in tr.events] == ["gateway", "bus", "core", "modern"]
        assert tr.root_id == CLI


def test_order_independent():
    ids = set()
    for perm in itertools.permutations(chain()):
        g = IdGraph()
        for ev in perm:
            g.add(ev)
        tr = g.trace(CORE)
        assert tr is not None and tr.complete
        ids.add(tr.trace_id)
    assert len(ids) == 1  # el trace_id canónico es determinista


def test_duplicates_are_ignored():
    g = IdGraph()
    evs = chain()
    assert all(g.add(e) for e in evs)
    assert not any(g.add(e) for e in evs)
    assert len(g) == 4


def test_missing_layer_is_reported_not_invented():
    g = IdGraph()
    for ev in chain():
        if ev.layer != "bus":
            g.add(ev)
    tr = g.trace(CLI)
    assert tr is not None and not tr.complete
    assert tr.missing_layers == ["bus", "core", "modern"] or "bus" in tr.missing_layers
    assert {e.layer for e in tr.events} == {"gateway"}  # no se unen gateway y core sin el eslabón del bus


def test_two_transactions_do_not_mix():
    g = IdGraph()
    for ev in chain("1") + chain("2"):
        g.add(ev)
    a, b = g.trace("CLI-000000000001"), g.trace("CLI-000000000002")
    assert a.trace_id != b.trace_id
    assert {e.id for e in a.events}.isdisjoint({e.id for e in b.events})


def test_unknown_id_returns_none():
    assert IdGraph().trace("GW-ffffffffffff") is None


def test_eviction_bounds_memory():
    g = IdGraph(max_events=8)
    for i in range(1, 6):
        for ev in chain(str(i), t=1000.0 + i):  # cada transacción con su hora; se descartan las más viejas
            g.add(ev)
    assert len(g) == 8
    assert g.trace("CLI-000000000001") is None or not g.trace("CLI-000000000001").complete
    assert g.trace("CLI-000000000005").complete


def test_completeness_counts_roots():
    g = IdGraph()
    for ev in chain("1") + [e for e in chain("2") if e.layer != "core"]:
        g.add(ev)
    assert g.completeness() == {"total": 2, "complete": 1}


def test_cycle_does_not_hang():
    g = IdGraph()
    g.add(Event("bus", BUS, GW, 1.0, 1, "200"))
    g.add(Event("gateway", GW, BUS, 1.0, 1, "200"))  # ciclo inválido (datos corruptos)
    assert g.trace(BUS) is not None


def test_core_failure_does_not_expect_modern_layer():
    g = IdGraph()
    for ev in chain():
        if ev.layer == "modern":
            continue
        g.add(ev if ev.layer != "core" else Event("core", ev.id, ev.parent_id, ev.end_ts, ev.duration_ms, "91"))
    tr = g.trace(CLI)
    assert tr is not None and tr.complete


def test_replay_at_uneven_speeds_keeps_recent_traces():
    """Reinicio del correlacionador con más historia que MAX_EVENTS (hallazgo de las pruebas en AWS).

    Al releer los logs, cada archivo avanza a un ritmo distinto (1 MB del core son muchos más registros
    que 1 MB de spans del moderno). Con evicción por orden de llegada se botaban eventos recientes de unas
    capas mientras seguían entrando eventos viejos de otras, y no quedaba ninguna traza completa.
    La evicción por hora del evento conserva las transacciones más recientes completas.
    """
    n, keep = 400, 100  # 400 transacciones de historia; caben 100 (4 eventos cada una)
    chains = [chain(f"{i:x}", t=1000.0 + i) for i in range(1, n + 1)]
    by_layer = {layer: [c[k] for c in chains] for k, layer in enumerate(("modern", "core", "bus", "gateway"))}
    speed = {"core": 40, "gateway": 20, "bus": 8, "modern": 5}  # eventos por lectura, como en AWS
    g = IdGraph(max_events=4 * keep)
    pos = {layer: 0 for layer in speed}
    while any(pos[la] < n for la in speed):
        for layer, step in speed.items():
            for ev in by_layer[layer][pos[layer] : pos[layer] + step]:
                g.add(ev)
            pos[layer] = min(n, pos[layer] + step)
    assert len(g) == 4 * keep
    recent = [f"CLI-{i:x}".replace("CLI-", "CLI-" + "0" * (12 - len(f"{i:x}"))) for i in range(n - keep // 2, n + 1)]
    assert all(g.trace(cli) is not None and g.trace(cli).complete for cli in recent)
    assert g.completeness()["complete"] >= keep - 1
