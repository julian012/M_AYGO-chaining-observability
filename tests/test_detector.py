"""Detector de anomalías: fuente silenciosa, cadena rota y latencia, cada una nombrando la capa."""

from obsproto.correlator.detector import Detector
from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import Event

NOW = 10_000.0


def make(sources, graph=None):
    g = graph or IdGraph()
    d = Detector(g, sources, ("gateway", "bus", "core", "modern"), warmup_s=0)
    d._started = NOW - 100
    return g, d


def tx(g, d, i, t, layers=("gateway", "bus", "core", "modern"), core_ms=20):
    s = f"{i:012x}"
    evs = {
        "gateway": Event("gateway", f"GW-{s}", f"CLI-{s}", t, core_ms + 4, "200"),
        "bus": Event("bus", f"BUS-{s}", f"GW-{s}", t - 0.001, core_ms + 2, "00"),
        "core": Event("core", f"CORE-{s}", f"BUS-{s}", t - 0.002, core_ms, "00"),
        "modern": Event("modern", f"MOD-{s}", f"CORE-{s}", t + 1, 0, "OK"),
    }
    for la in layers:
        g.add(evs[la])
        d.on_event(evs[la])


def healthy_sources(now=NOW):
    return lambda: {la: {"lastLine": now - 1, "error": None} for la in ("gateway", "bus", "core", "modern")}


def test_silent_source_fires_names_the_layer_and_resolves():
    state = {la: {"lastLine": NOW - 1, "error": None} for la in ("gateway", "bus", "core", "modern")}
    _, d = make(lambda: state)
    assert d.evaluate(NOW - 30) == []  # hay tráfico en el borde desde aquí
    state["bus"]["lastLine"] = NOW - 30  # el bus dejó de transmitir; el gateway sigue con tráfico
    (alert,) = d.evaluate(NOW)
    assert (alert["rule"], alert["layer"]) == ("source_silent", "bus")
    state["bus"]["lastLine"] = NOW + 1
    d.evaluate(NOW + 2)
    assert d.active()  # histéresis: hacen falta dos evaluaciones sanas
    d.evaluate(NOW + 4)
    assert not d.active() and d.history[-1]["state"] == "RESOLVED"


def test_unreadable_source_counts_as_silent():
    state = {la: {"lastLine": NOW - 1, "error": None} for la in ("gateway", "bus", "core", "modern")}
    state["core"]["error"] = "PermissionError"
    _, d = make(lambda: state)
    (alert,) = d.evaluate(NOW)
    assert alert["layer"] == "core" and "PermissionError" in alert["detail"]


def test_no_silent_alert_without_edge_traffic():
    state = {la: {"lastLine": NOW - 60, "error": None} for la in ("gateway", "bus", "core", "modern")}
    _, d = make(lambda: state)
    assert d.evaluate(NOW) == []  # sin tráfico en el borde, nadie "dejó de transmitir"


def test_chain_break_names_the_first_missing_layer():
    g, d = make(healthy_sources())
    for i in range(20):
        tx(g, d, i, NOW - 10 + i * 0.1, layers=("gateway", "core", "modern"))  # sin eslabón del bus
    alerts = {(a["rule"], a["layer"]) for a in d.evaluate(NOW)}
    assert ("chain_break", "bus") in alerts


def test_complete_chains_raise_nothing():
    g, d = make(healthy_sources())
    for i in range(20):
        tx(g, d, i, NOW - 10 + i * 0.1)
    assert d.evaluate(NOW) == []


def test_latency_points_to_the_deepest_layer():
    g, d = make(healthy_sources())
    i = 0
    for k in range(8):  # línea base: ~20 ms
        t0 = NOW - 200 + k * 10
        for j in range(20):
            tx(g, d, i, t0 + j * 0.2)
            i += 1
        d.evaluate(t0 + 5)
    assert not d.active()
    t0 = NOW - 100
    for j in range(20):  # el core se degrada: 400 ms (bus y gateway también suben, porque lo incluyen)
        tx(g, d, i, t0 + j * 0.2, core_ms=400)
        i += 1
    alerts = [a for a in d.evaluate(t0 + 5) if a["rule"] == "latency"]
    assert len(alerts) == 1 and alerts[0]["layer"] == "core" and "también afectadas" in alerts[0]["detail"]


def test_no_alerts_during_warmup():
    state = {la: {"lastLine": NOW - 1, "error": None} for la in ("gateway", "bus", "core", "modern")}
    state["bus"]["lastLine"] = NOW - 30
    d = Detector(IdGraph(), lambda: state, ("gateway", "bus", "core", "modern"), warmup_s=60)
    d._started = NOW - 10
    assert d.evaluate(NOW) == []


def test_downstream_silence_counts_from_when_edge_traffic_starts():
    # Pila recién arrancada: el moderno no tiene líneas porque antes no había tráfico, no porque esté caído.
    state = {la: {"lastLine": NOW - 120, "error": None} for la in ("gateway", "bus", "core", "modern")}
    _, d = make(lambda: state)
    assert d.evaluate(NOW - 100) == []  # sin tráfico en el borde
    for la in ("gateway", "bus", "core"):
        state[la]["lastLine"] = NOW
    assert d.evaluate(NOW) == []  # el tráfico acaba de empezar: el moderno aún no tiene por qué haber escrito
    for la in ("gateway", "bus", "core"):
        state[la]["lastLine"] = NOW + 30
    assert {a["layer"] for a in d.evaluate(NOW + 30)} == {"modern"}  # 30 s con tráfico y el moderno sigue mudo
