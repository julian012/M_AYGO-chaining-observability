#!/usr/bin/env python3
"""Experimento: ¿se puede unir el bus con un core que no recibe su ID (tipo Stratus)?

Para cada política de ofuscación del tracenumber en el log del bus (claro, token, máscara con 4/3/2 dígitos
visibles, nada): levanta la pila desde cero con ``CORE_LINK=tracenumber``, el bus en INFO con una línea resumen
por mensaje (``BUS_INFO_PROFILE=summary``, sin DEBUG, como en producción), envía carga y evalúa:
  - % de trazas completas (desde el ID del cliente),
  - uniones correctas, FALSAS y no unidas contra la verdad de referencia (``truth.log``),
  - lo que reporta el correlacionador (``coreLink``: unidas, ambiguas, sin pareja).

Uso (en el host donde corre docker compose, desde la raíz del repo):
  python3 scripts/core_link_experiment.py --n 1500 --concurrency 20 --out-dir evidence/core-link-local-<fecha>
Al terminar deja .env como estaba (modo frame).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

POLICIES = [  # (etiqueta, BUS_TRACE_LOG, dígitos visibles)
    ("clear", "clear", 4),
    ("token", "token", 4),
    ("mask4", "mask", 4),
    ("mask3", "mask", 3),
    ("mask2", "mask", 2),
    ("none", "none", 4),
]
DOCKER = shutil.which("docker") or "/usr/bin/docker"


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(list(args), capture_output=True, text=True, check=check).stdout  # noqa: S603


def set_env(path: Path, values: dict) -> None:
    lines = path.read_text().splitlines()
    keys = set(values)
    out = [ln for ln in lines if ln.split("=", 1)[0] not in keys]
    out += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(out) + "\n")


def wait_healthy(n: int = 5) -> None:
    for _ in range(80):
        health = sh(DOCKER, "compose", "ps", "--format", "{{.Health}}", check=False).split()
        if health.count("healthy") >= n:
            return
        time.sleep(3)
    sys.exit("la pila no quedó sana")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--url", default="https://127.0.0.1:8443")
    ap.add_argument("--only", default="", help="políticas separadas por coma (por defecto, todas)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    env = Path(".env")
    original = env.read_text()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    gen = ["scripts/generate_load.py", "--url", args.url, "--cacert", "certs/server.crt"]
    selected = [p for p in POLICIES if not args.only or p[0] in args.only.split(",")]
    rows = []
    try:
        for label, policy, digits in selected:
            print(f"\n== {label}: BUS_TRACE_LOG={policy} dígitos={digits}", flush=True)
            set_env(
                env,
                {
                    "CORE_LINK": "tracenumber",
                    "BUS_INFO_PROFILE": "summary",
                    "BUS_LOG_LEVEL": "INFO",
                    "BUS_TRACE_LOG": policy,
                    "BUS_TRACE_MASK_DIGITS": str(digits),
                },
            )
            sh(DOCKER, "compose", "down", "-v", check=False)
            sh(DOCKER, "compose", "up", "-d")
            wait_healthy()
            sh(py, *gen, "--n", "200", "--concurrency", "5")  # calentamiento
            run = json.loads(
                sh(
                    py,
                    *gen,
                    "--n",
                    str(args.n),
                    "--concurrency",
                    str(args.concurrency),
                    "--correlator",
                    "http://127.0.0.1:9100",
                    "--settle",
                    "8",
                )
            )
            truth = out / f"truth-{label}.log"
            sh(DOCKER, "compose", "cp", "bus:/var/log/app/truth.log", str(truth))
            ev = json.loads(sh(py, "scripts/eval_core_link.py", "--truth", str(truth), "--policy", label))
            truth.unlink()  # la verdad no se guarda como evidencia (no aporta y crece mucho)
            sample = sh(DOCKER, "compose", "exec", "-T", "bus", "sh", "-c", "tail -1 /var/log/app/bus.log").strip()
            result = {
                "policy": label,
                "busTraceLog": policy,
                "maskDigits": digits if policy == "mask" else None,
                "load": {k: run[k] for k in ("requests", "throughputRps", "latencyMs", "statusCounts")},
                "completeTraces": run.get("correlation"),
                "linkEval": ev,
                "busLogSample": sample,
            }
            (out / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
            c = run.get("correlation") or {}
            cl = ev.get("correlatorCoreLink") or {}
            rows.append(
                (
                    label,
                    run["throughputRps"],
                    c.get("completePct"),
                    ev["correct"],
                    ev["wrong"],
                    ev["unlinked"],
                    cl.get("ambiguous"),
                    cl.get("unmatched"),
                )
            )
            print(
                f"   rps {run['throughputRps']} · completas {c.get('completePct')} % · correctas {ev['correct']} · "
                f"FALSAS {ev['wrong']} · no unidas {ev['unlinked']} · ambiguas {cl.get('ambiguous')}",
                flush=True,
            )
            print(f"   bus.log: {sample}", flush=True)
    finally:
        env.write_text(original)
        sh(DOCKER, "compose", "down", "-v", check=False)
        sh(DOCKER, "compose", "up", "-d", check=False)
    print("\npolítica | rps | % completas | correctas | falsas | no unidas | ambiguas | sin pareja")
    for r in rows:
        print(" | ".join(str(x) for x in r))


if __name__ == "__main__":
    main()
