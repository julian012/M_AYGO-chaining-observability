#!/usr/bin/env python3
"""Tiempo hasta traza completa: cuánto tarda en llegar el último tramo (core -> moderno por archivo plano/FTP).

El cliente recibe su respuesta cuando el core contesta la trama; el servicio moderno se entera después, al procesar
el lote. Para una muestra de transacciones se consulta ``/trace`` y se mide, en la traza reconstruida, la distancia
entre el fin del span del gateway (respuesta al cliente) y el fin del span del moderno.

Uso (en la instancia de la aplicación, después de una corrida):
  python3 scripts/trace_completion.py --ids /tmp/obsproto-run/normal-c20-r1.ids --sample 300 --out /tmp/x.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", required=True, help="archivo de IDs de una corrida (--ids-out del generador)")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--correlator", default="http://127.0.0.1:9100")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    token = next(
        (
            ln.split("=", 1)[1].strip()
            for ln in Path(args.env_file).read_text().splitlines()
            if ln.startswith("CORRELATOR_TOKEN=")
        ),
        "",
    )
    ids = json.loads(Path(args.ids).read_text())["ids"]
    sample = random.sample(ids, min(args.sample, len(ids)))  # noqa: S311

    delays, to_mft, mft_to_modern, without_modern = [], [], [], 0
    for cid in sample:
        req = urllib.request.Request(
            f"{args.correlator.rstrip('/')}/trace/{cid}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback
                spans = {s["layer"]: s for s in json.loads(resp.read())["spans"]}
        except (urllib.error.URLError, KeyError, ValueError):
            continue
        if "modern" not in spans or "gateway" not in spans:
            without_modern += 1
            continue
        gw_end = spans["gateway"]["startOffsetMs"] + spans["gateway"]["durationMs"]
        mod_end = spans["modern"]["startOffsetMs"] + spans["modern"]["durationMs"]
        delays.append((mod_end - gw_end) / 1000)
        if "mft" in spans:  # con el eslabón MFT: separar el tramo en (lote del core + job MFT) y (revisión del moderno)
            mft_end = spans["mft"]["startOffsetMs"] + spans["mft"]["durationMs"]
            to_mft.append((mft_end - gw_end) / 1000)
            mft_to_modern.append((mod_end - mft_end) / 1000)

    def dist(v: list):
        v = sorted(v)
        if not v:
            return None
        return {
            "min": round(v[0], 2),
            "median": round(statistics.median(v), 2),
            "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))], 2),
            "max": round(v[-1], 2),
        }

    delays.sort()
    summary = {
        "sample": len(sample),
        "withModern": len(delays),
        "withoutModern": without_modern,
        "secondsAfterResponse": {
            "min": round(delays[0], 2),
            "median": round(statistics.median(delays), 2),
            "p95": round(delays[min(len(delays) - 1, int(len(delays) * 0.95))], 2),
            "max": round(delays[-1], 2),
        }
        if delays
        else None,
        "responseToMftMoved": dist(to_mft),
        "mftMovedToModern": dist(mft_to_modern),
        "note": "lote del core (5 s) + job del MFT (3 s, si existe) + revisión del moderno (2 s)",
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
