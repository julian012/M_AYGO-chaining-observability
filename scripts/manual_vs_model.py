#!/usr/bin/env python3
"""Seguimiento manual frente al modelo: ¿cuánto cuesta reconstruir una transacción?

Manual: lo que hace un operador sin el correlacionador. Con el ID del cliente busca en el log del gateway
(``grep``), saca el ID del gateway, lo busca en el log del bus, saca el ID del bus, lo busca en el registro
de ancho fijo del core (cortando por posición) y por último busca el ID del core en los spans del servicio
moderno. Son 4 búsquedas en 4 archivos con 4 formatos distintos, y el tiempo crece con el tamaño de los logs.

Modelo: una consulta ``GET /trace/<CLI-…>`` al correlacionador.

El tiempo medido es el de la máquina (grep). El de una persona es mayor: cada paso implica entrar a otro
sistema y leer otro formato. Por eso también se reportan los pasos y los sistemas consultados.

Uso (en la instancia de la aplicación, como root para leer los volúmenes de Docker):
  sudo python3 scripts/manual_vs_model.py --samples 20 --out /tmp/manual-vs-model.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LAYERS = ("gateway", "bus", "core", "modern")


def volume_dir(layer: str) -> Path:
    docker = shutil.which("docker") or "/usr/bin/docker"
    out = subprocess.run(  # noqa: S603 - comando fijo
        [docker, "volume", "inspect", f"obsproto_logs-{layer}", "--format", "{{.Mountpoint}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(out)


def grep(pattern: str, path: Path) -> tuple:
    """Devuelve (primera línea que coincide, ms). Usa grep -F -m1, como lo haría un operador."""
    g = shutil.which("grep") or "/usr/bin/grep"
    t0 = time.perf_counter()
    res = subprocess.run([g, "-F", "-m1", pattern, str(path)], capture_output=True, text=True)  # noqa: S603
    return res.stdout.strip(), (time.perf_counter() - t0) * 1000


def manual_chain(cli: str, logs: dict) -> dict:
    steps, ids = [], {"cli": cli}
    line, ms = grep(f"in_id={cli} ", logs["gateway"])  # 1) access log del gateway
    steps.append(ms)
    m = re.search(r"tid=(GW-[0-9a-f]{12})", line)
    if m:
        ids["gw"] = m[1]
        line, ms = grep(f"upstream={ids['gw']} out=", logs["bus"])  # 2) línea DEBUG del bus
        steps.append(ms)
        m = re.search(r"msgId=(BUS-[0-9a-f]{12})", line)
    if m and "gw" in ids:
        ids["bus"] = m[1]
        line, ms = grep(ids["bus"].ljust(20), logs["core"])  # 3) registro de ancho fijo: el padre en 37-56
        steps.append(ms)
        if len(line) >= 57 and line[37:57].strip() == ids["bus"]:
            ids["core"] = line[17:37].strip()
    if "core" in ids:
        line, ms = grep(f'"upstream.id":"{ids["core"]}"', logs["modern"])  # 4) span OTel del moderno
        steps.append(ms)
        m = re.search(r'"app.id":"(MOD-[0-9a-f]{12})"', line)
        if m:
            ids["mod"] = m[1]
    return {"ids": ids, "steps": len(steps), "ms": sum(steps), "complete": "mod" in ids}


def model_trace(cli: str, url: str, token: str) -> dict:
    req = urllib.request.Request(f"{url.rstrip('/')}/trace/{cli}", headers={"Authorization": f"Bearer {token}"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        data = {}
    return {"ms": (time.perf_counter() - t0) * 1000, "complete": bool(data.get("complete"))}


def stats(values: list) -> dict:
    v = sorted(values)
    return {
        "median": round(statistics.median(v), 2),
        "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))], 2),
        "max": round(v[-1], 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--correlator", default="http://127.0.0.1:9100")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parent.parent / ".env"))
    ap.add_argument(
        "--log-dir", default="", help="carpeta con gateway/ bus/ core/ modern/ (por defecto, los volúmenes)"
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.log_dir:
        logs = {la: Path(args.log_dir) / la / f"{la}.log" for la in LAYERS}
    else:
        logs = {la: volume_dir(la) / f"{la}.log" for la in LAYERS}
    token = next(
        (
            ln.split("=", 1)[1].strip()
            for ln in Path(args.env_file).read_text().splitlines()
            if ln.startswith("CORRELATOR_TOKEN=")
        ),
        os.environ.get("CORRELATOR_TOKEN", ""),
    )

    # Muestra: transacciones exitosas del access log del gateway (el operador parte del ID que le da el cliente).
    ok_clis = re.findall(r"in_id=(CLI-[0-9a-f]{12}) .*status=200 ", logs["gateway"].read_text())
    if not ok_clis:
        sys.exit("No hay transacciones en el log del gateway.")
    sample = random.sample(ok_clis, min(args.samples, len(ok_clis)))  # noqa: S311

    manual = [manual_chain(c, logs) for c in sample]
    model = [model_trace(c, args.correlator, token) for c in sample]
    summary = {
        "samples": len(sample),
        "logSizes": {
            la: {"MB": round(p.stat().st_size / 2**20, 1), "lines": sum(1 for _ in p.open("rb"))}
            for la, p in logs.items()
        },
        "manual": {
            "stepsPerTransaction": 4,
            "systemsAndFormats": "gateway (access log clave=valor), bus (texto con niveles, línea DEBUG), "
            "core (ancho fijo por posición), moderno (span OTel)",
            "machineMs": stats([m["ms"] for m in manual]),
            "completeChains": sum(m["complete"] for m in manual),
        },
        "model": {
            "queriesPerTransaction": 1,
            "machineMs": stats([m["ms"] for m in model]),
            "completeTraces": sum(m["complete"] for m in model),
        },
        "example": manual[0]["ids"],
    }
    summary["speedupMedian"] = round(
        summary["manual"]["machineMs"]["median"] / max(summary["model"]["machineMs"]["median"], 0.01), 1
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
