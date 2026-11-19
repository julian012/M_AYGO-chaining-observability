#!/usr/bin/env python3
"""Consulta histórica: reconstruye trazas desde los logs archivados, sin el límite de memoria del correlacionador.

El correlacionador en línea guarda en memoria una ventana de transacciones recientes (``MAX_EVENTS``). Para mirar
históricos, esta herramienta relee los logs con los mismos parsers y arma el grafo completo: el alcance pasa a depender
de la **política de retención de los logs**, no de la memoria.

Además simula una retención desalineada: si una dependencia conserva menos historia que las demás (``--keep-pct``),
las trazas antiguas quedan incompletas justo en esa capa. Reporta la completitud por antigüedad (deciles).

Uso:
  python3 scripts/replay_history.py --log-dir <carpeta con gateway/ bus/ core/ mft/ modern/> --out x.json
  python3 scripts/replay_history.py --log-dir … --keep-pct bus=50 --out y.json
  python3 scripts/replay_history.py --log-dir … --trace CLI-xxxxxxxxxxxx
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from obsproto.correlator.corelink import CoreLinker  # noqa: E402
from obsproto.correlator.filelink import TransferIndex  # noqa: E402
from obsproto.correlator.graph import IdGraph  # noqa: E402
from obsproto.correlator.parsers import (  # noqa: E402
    BusLogParser,
    Event,
    MftLogParser,
    MftTransfer,
    TraceRef,
    parse_core_line,
    parse_gateway_line,
    parse_span_line,
)


def load(log_dir: Path, keep_pct: dict, key: str = "") -> tuple:
    has_mft = (log_dir / "mft" / "mft.log").exists()
    transfers = TransferIndex()
    graph = IdGraph(
        max_events=10**9,
        layers=("gateway", "bus", "core", "mft", "modern") if has_mft else None,
        side=transfers.side_event if has_mft else None,
    )
    linker = CoreLinker(graph, key)
    sources = [
        ("gateway", parse_gateway_line),
        ("bus", BusLogParser()),
        ("core", parse_core_line),
        ("modern", parse_span_line),
    ] + ([("mft", MftLogParser())] if has_mft else [])
    kept = {}
    for layer, parse in sources:
        lines = (log_dir / layer / f"{layer}.log").read_text(encoding="utf-8", errors="replace").splitlines()
        pct = keep_pct.get(layer, 100)
        lines = lines[len(lines) - int(len(lines) * pct / 100) :]  # retención: solo la parte más reciente
        kept[layer] = {"lines": len(lines), "keptPct": pct}
        for line in lines:
            out = parse(line)
            for item in [] if out is None else ([out] if isinstance(out, Event) else out):
                if isinstance(item, TraceRef):
                    linker.on_ref(item)
                elif isinstance(item, MftTransfer):
                    transfers.add(item)
                elif graph.add(item) and item.layer == "core":
                    linker.on_core_event(item)
    linker.resolve(now=float("inf"), core_caught_up=True)
    return graph, kept


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument(
        "--keep-pct", default="", help="retención por fuente, p. ej. bus=50 (conserva el 50 %% más reciente)"
    )
    ap.add_argument("--trace", default="", help="mostrar la traza de este ID")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    keep = {k: float(v) for k, v in (p.split("=") for p in a.keep_pct.split(",") if p)}

    tracemalloc.start()
    t0 = time.perf_counter()
    graph, kept = load(Path(a.log_dir), keep)
    build_s = time.perf_counter() - t0
    mem_mb = tracemalloc.get_traced_memory()[1] / 2**20

    if a.trace:
        tr = graph.trace(a.trace)
        print(json.dumps(tr.to_dict() if tr else {"error": "no encontrada"}, indent=2))
        return

    # Antigüedad: orden del log del gateway (la primera línea es la transacción más vieja).
    gw = (Path(a.log_dir) / "gateway" / "gateway.log").read_text().splitlines()
    clis = [ev.parent_id for ln in gw if (ev := parse_gateway_line(ln)) and ev.status == "200"]
    deciles = []
    missing: dict = {}
    for d in range(10):
        part = clis[d * len(clis) // 10 : (d + 1) * len(clis) // 10]
        ok = 0
        for cid in part:
            tr = graph.trace(cid)
            if tr and tr.complete:
                ok += 1
            elif tr:
                key = "+".join(tr.missing_layers)
                missing[key] = missing.get(key, 0) + 1
        deciles.append(round(100 * ok / len(part), 1) if part else None)
    total_ok = sum(1 for cid in clis if (tr := graph.trace(cid)) and tr.complete)
    summary = {
        "transactions": len(clis),
        "complete": total_ok,
        "completePct": round(100 * total_ok / len(clis), 2) if clis else None,
        "completePctByAgeDecile_oldestFirst": deciles,
        "incompleteByMissingLayers": missing,
        "sources": kept,
        "rebuildSeconds": round(build_s, 2),
        "peakMemoryMB": round(mem_mb, 1),
        "events": len(graph),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
