#!/usr/bin/env python3
"""Monitor del host: cada N segundos publica en CloudWatch lo que pasa dentro de la pila.

- Correlacionador (``/stats``): transacciones, trazas completas, % de completitud, líneas
  descartadas, respuestas del bus sin padre y eventos por capa.
- Contenedores (``docker stats``): CPU (%) y memoria (MB) de cada servicio.

Se ejecuta en la instancia de la aplicación como servicio systemd (``infra/obsproto-monitor.service``)
o a mano:  sudo python3 scripts/monitor_host.py --interval 10
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cw_metrics import datum, publish  # noqa: E402

_UNITS = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "kB": 1 / 1024, "MB": 1.0, "GB": 1024.0}


def load_token(env_file: str) -> str:
    for line in Path(env_file).read_text().splitlines():
        if line.startswith("CORRELATOR_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("CORRELATOR_TOKEN", "")


def correlator_metrics(url: str, token: str, ts: float) -> list:
    req = urllib.request.Request(url.rstrip("/") + "/stats", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback fijo
        s = json.loads(resp.read())
    data = [
        datum("Transactions", s.get("transactions") or 0, "Count", ts, Source="correlator"),
        datum("CompleteTraces", s.get("completeTraces") or 0, "Count", ts, Source="correlator"),
        datum("CompletenessPct", 100 * (s.get("completeness") or 0), "Percent", ts, Source="correlator"),
        datum("MalformedLines", s.get("malformedLines") or 0, "Count", ts, Source="correlator"),
        datum("BusUnlinkedReplies", s.get("busUnlinkedReplies") or 0, "Count", ts, Source="correlator"),
    ]
    data += [datum("EventsIngested", v, "Count", ts, Layer=k) for k, v in (s.get("events") or {}).items()]
    return data


def _mem_mb(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    return float(m[1]) * _UNITS.get(m[2], 1.0) if m else 0.0


def container_metrics(ts: float) -> list:
    docker = shutil.which("docker") or "/usr/bin/docker"
    out = subprocess.run(  # noqa: S603 - comando fijo, sin shell
        [docker, "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True, text=True, timeout=30
    ).stdout
    data = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("Name", "").replace("obsproto-", "").rsplit("-", 1)[0]  # obsproto-bus-1 -> bus
        cpu = float(row.get("CPUPerc", "0%").rstrip("%") or 0)
        data.append(datum("CPUPercent", cpu, "Percent", ts, Container=name))
        data.append(
            datum("MemoryMB", _mem_mb(row.get("MemUsage", "0B").split("/")[0]), "Megabytes", ts, Container=name)
        )
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--correlator", default="http://127.0.0.1:9100")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parent.parent / ".env"))
    ap.add_argument("--once", action="store_true", help="una sola muestra (para probar)")
    args = ap.parse_args()
    token = load_token(args.env_file)

    while True:
        ts = time.time()
        data = []
        try:
            data += correlator_metrics(args.correlator, token, ts)
        except Exception as exc:  # noqa: BLE001 - el monitor nunca debe caerse
            print(f"[monitor] correlacionador no disponible: {exc}", flush=True)
            data.append(datum("CorrelatorUp", 0, "Count", ts, Source="correlator"))
        else:
            data.append(datum("CorrelatorUp", 1, "Count", ts, Source="correlator"))
        try:
            data += container_metrics(ts)
        except Exception as exc:  # noqa: BLE001
            print(f"[monitor] docker stats falló: {exc}", flush=True)
        publish(data)
        if args.once:
            print(json.dumps(data[:3], indent=2))
            return
        time.sleep(max(0.0, args.interval - (time.time() - ts)))


if __name__ == "__main__":
    main()
