#!/usr/bin/env python3
"""Evalúa la unión bus -> core (modo tracenumber) contra la verdad de referencia.

El bus escribe en ``truth.log`` el par real ``BUS-… CORE-<tracenumber>`` (lo sabe porque recibe el tracenumber
en claro en la respuesta). El correlacionador NUNCA lee ese archivo. Aquí se compara, para cada movimiento,
con qué bus quedó unido el registro del core en la traza reconstruida:

  correct   la traza del core tiene como padre el bus verdadero
  wrong     la traza del core quedó unida a OTRO bus (enlace falso)
  unlinked  el core quedó sin padre (la traza se reporta incompleta)

Uso (en el host de la aplicación; los IDs se leen de la verdad, no del log):
  python3 scripts/eval_core_link.py --truth truth.log --policy mask4 --out /tmp/eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import urllib.error
import urllib.request
from pathlib import Path


def load_token(env_file: str) -> str:
    p = Path(env_file)
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("CORRELATOR_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("CORRELATOR_TOKEN", "")


def get(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--policy", default="", help="etiqueta del escenario (clear, token, mask4, …)")
    ap.add_argument("--correlator", default="http://127.0.0.1:9100")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--sample", type=int, default=0, help="evaluar solo N movimientos al azar (0 = todos)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    token = load_token(args.env_file)

    pairs = [ln.split() for ln in Path(args.truth).read_text().splitlines() if ln.strip()]
    pairs = [p for p in pairs if len(p) == 2]
    if args.sample and len(pairs) > args.sample:
        pairs = random.sample(pairs, args.sample)  # noqa: S311

    counts = {"correct": 0, "wrong": 0, "unlinked": 0, "missing": 0}
    wrong_examples = []
    for bus_true, core in pairs:
        tr = get(f"{args.correlator.rstrip('/')}/trace/{core}", token)
        if tr is None:
            counts["missing"] += 1  # el registro del core no está en memoria
            continue
        bus = next((s for s in tr["spans"] if s["layer"] == "bus"), None)
        if bus is None:
            counts["unlinked"] += 1
        elif bus["id"] == bus_true:
            counts["correct"] += 1
        else:
            counts["wrong"] += 1
            if len(wrong_examples) < 5:
                wrong_examples.append({"core": core, "true": bus_true, "linkedTo": bus["id"]})

    n = len(pairs)
    stats = get(f"{args.correlator.rstrip('/')}/stats", token) or {}
    summary = {
        "policy": args.policy,
        "evaluated": n,
        **counts,
        "correctPct": round(100 * counts["correct"] / n, 2) if n else None,
        "wrongPct": round(100 * counts["wrong"] / n, 2) if n else None,
        "unlinkedPct": round(100 * counts["unlinked"] / n, 2) if n else None,
        "wrongExamples": wrong_examples,
        "correlatorCoreLink": stats.get("coreLink"),
        "completeness": stats.get("completeness"),
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
