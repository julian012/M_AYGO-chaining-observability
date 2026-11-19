#!/usr/bin/env python3
"""Experimento de anomalías: ¿cuándo detecta la falla el sistema centralizado y qué ve el negocio?

Con carga continua, para cada anomalía: línea base, inyección, sostenimiento, reversión y recuperación. Se mide:
  - **sistema centralizado:** primera alerta del detector (``/alerts``) después de la inyección, su regla y su capa,
    y cuándo se resolvió después de la reversión;
  - **cliente (la cara del negocio):** peticiones, % de errores y p50/p95 antes, durante y después;
  - para la fuente que deja de transmitir: cuánto tarda el correlacionador en ponerse al día y completar las
    trazas del periodo en que la fuente estuvo silenciada (relleno retroactivo).

Anomalías (todas en caliente, sin reiniciar la capa afectada salvo el MFT):
  core-slow      el core tarda 300 ms más por movimiento                 → latencia, el cliente lo nota
  mft-stopped    se detiene el MFT (no mueve archivos)                     → falla silenciosa para el cliente
  bus-info       el bus deja de escribir DEBUG (cambio de configuración)   → se rompe la trazabilidad, no el negocio
  bus-silent     el log del bus se vuelve ilegible para el correlacionador → la fuente deja de transmitir

Uso (desde la raíz del repo, con la pila arriba):
  python3 scripts/anomaly_experiment.py --out-dir evidence/anomalies-local-<fecha> [--only core-slow,bus-silent]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_load as gl  # noqa: E402

DOCKER = shutil.which("docker") or "/usr/bin/docker"
ANOMALIES = {
    "core-slow": (
        ["exec", "-T", "core", "sh", "-c", "echo '{\"extra_latency_ms\": 300}' > /var/log/app/chaos.json"],
        ["exec", "-T", "core", "rm", "-f", "/var/log/app/chaos.json"],
    ),
    "mft-stopped": (["stop", "mft"], ["start", "mft"]),
    "bus-info": (
        ["exec", "-T", "bus", "sh", "-c", 'echo \'{"log_level": "INFO"}\' > /var/log/app/chaos.json'],
        ["exec", "-T", "bus", "rm", "-f", "/var/log/app/chaos.json"],
    ),
    "bus-silent": (
        ["exec", "-T", "bus", "chmod", "000", "/var/log/app/bus.log"],
        ["exec", "-T", "bus", "chmod", "644", "/var/log/app/bus.log"],
    ),
}


def compose(*args: str) -> None:
    subprocess.run([DOCKER, "compose", *args], check=True, capture_output=True)  # noqa: S603


def get(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback
        return json.loads(resp.read())


class Load:
    """Carga continua; guarda cada respuesta con su hora."""

    def __init__(self, url: str, cacert: str, concurrency: int) -> None:
        gl.load_dotenv()
        self.keys = gl.channel_keys()
        self.args = argparse.Namespace(url=url, timeout=10.0, rest_only=False)
        self.ctx = ssl.create_default_context(cafile=cacert)
        self.results: list = []
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._run, args=(i,), daemon=True) for i in range(concurrency)]

    def _run(self, i: int) -> None:
        channels = sorted(self.keys)
        n = i
        while not self.stop.is_set():
            self.results.append(gl.one_request(self.args, self.ctx, self.keys, channels[n % len(channels)]))
            n += len(self.threads)

    def start(self) -> None:
        for t in self.threads:
            t.start()

    def halt(self) -> None:
        self.stop.set()
        for t in self.threads:
            t.join()

    def window(self, lo: float, hi: float) -> dict:
        rs = [r for r in self.results if lo <= r["t"] < hi]
        ok = sorted(r["ms"] for r in rs if r["status"] == 200)
        p = (lambda q: round(ok[min(len(ok) - 1, int(len(ok) * q))], 1)) if ok else (lambda q: None)
        return {
            "requests": len(rs),
            "errorPct": round(100 * (len(rs) - len(ok)) / len(rs), 2) if rs else None,
            "p50": round(statistics.median(ok), 1) if ok else None,
            "p95": p(0.95),
            "ids": [r["client_id"] for r in rs if r["status"] in (200, 500, 502)],
        }


def completeness(ids: list, correlator: str, token: str, sample: int = 150) -> float:
    ids = ids[-sample:]
    ok = 0
    for cid in ids:
        with contextlib.suppress(Exception):  # 404 = la traza no existe: cuenta como no completa
            ok += bool(get(f"{correlator}/trace/{cid}", token).get("complete"))
    return round(100 * ok / len(ids), 1) if ids else 0.0


def run_one(name: str, load: Load, a: argparse.Namespace, token: str) -> dict:
    inject, revert = ANOMALIES[name]
    print(f"\n== {name}: línea base {a.baseline}s · anomalía {a.hold}s · recuperación {a.recovery}s", flush=True)
    t_base = time.time()
    time.sleep(a.baseline)
    t_inj = time.time()
    compose(*inject)
    fired: dict = {}
    while time.time() - t_inj < a.hold:
        for h in get(f"{a.correlator}/alerts", token)["history"]:
            key = f"{h['rule']}:{h['layer']}"
            if h["state"] == "FIRING" and h["at"] >= t_inj and key not in fired:
                fired[key] = {"firedAfterS": round(h["at"] - t_inj, 1), "detail": h["detail"]}
                print(f"   alerta {key} a los {fired[key]['firedAfterS']} s: {h['detail']}", flush=True)
        time.sleep(1)
    t_rev = time.time()
    compose(*revert)
    resolved: dict = {}
    while time.time() - t_rev < a.recovery:
        for h in get(f"{a.correlator}/alerts", token)["history"]:
            key = f"{h['rule']}:{h['layer']}"
            if h["state"] == "RESOLVED" and h["at"] >= t_rev and key in fired and key not in resolved:
                resolved[key] = round(h["at"] - t_rev, 1)
        time.sleep(1)
    during = load.window(t_inj, t_rev)
    result = {
        "anomaly": name,
        "alerts": {k: {**v, "resolvedAfterRevertS": resolved.get(k)} for k, v in fired.items()},
        "timeToDetectS": min((v["firedAfterS"] for v in fired.values()), default=None),
        "client": {
            "before": {k: v for k, v in load.window(t_base, t_inj).items() if k != "ids"},
            "during": {k: v for k, v in during.items() if k != "ids"},
            "after": {k: v for k, v in load.window(t_rev + 10, time.time()).items() if k != "ids"},
        },
        # Toda alerta que se dispare durante la línea base (sin anomalía inyectada) es una falsa alarma.
        "falseAlarmsInBaseline": [
            f"{h['rule']}:{h['layer']}"
            for h in get(f"{a.correlator}/alerts", token)["history"]
            if h["state"] == "FIRING" and t_base <= h["at"] < t_inj
        ],
    }
    # Trazas del periodo de la anomalía: ¿se completan después? (relleno retroactivo cuando la fuente vuelve)
    result["traceCompletenessOfAnomalyWindow"] = {
        "atRevertPlusRecovery": completeness(during["ids"], a.correlator, token)
    }
    print(
        f"   cliente durante: {result['client']['during']} · trazas del periodo completas: "
        f"{result['traceCompletenessOfAnomalyWindow']['atRevertPlusRecovery']} %",
        flush=True,
    )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://127.0.0.1:8443")
    ap.add_argument("--cacert", default="certs/server.crt")
    ap.add_argument("--correlator", default="http://127.0.0.1:9100")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--hold", type=float, default=60)
    ap.add_argument("--recovery", type=float, default=45)
    ap.add_argument("--only", default="")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    token = next(
        ln.split("=", 1)[1].strip()
        for ln in Path(".env").read_text().splitlines()
        if ln.startswith("CORRELATOR_TOKEN=")
    )
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    load = Load(a.url, a.cacert, a.concurrency)
    load.start()
    try:
        for name in [n for n in ANOMALIES if not a.only or n in a.only.split(",")]:
            res = run_one(name, load, a, token)
            (out / f"{name}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        load.halt()


if __name__ == "__main__":
    main()
