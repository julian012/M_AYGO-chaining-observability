#!/usr/bin/env python3
"""Generador de carga y verificador de correlación (solo biblioteca estándar).

Envía transacciones sintéticas por los tres canales (móvil, web, API de tercero) y, al
final, le pregunta al correlacionador cuántas transacciones se reconstruyeron de punta
a punta. Es la métrica principal del proyecto: % de trazas completas.

Ejemplo:
  python scripts/generate_load.py --url https://127.0.0.1:8443 --cacert certs/server.crt \
      --n 500 --concurrency 20 --correlator http://127.0.0.1:9100
Las claves se leen de las variables de entorno / archivo .env (nunca por argumento).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def channel_keys() -> dict:
    keys = {}
    for item in os.environ.get("API_KEYS", "").split(","):
        channel, _, key = item.strip().partition(":")
        if channel and key:
            keys[channel] = key
    return keys


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
TXN_NS = "urn:obsproto:txn:v1"


def soap_body(txn: dict, client_id: str) -> bytes:
    return (
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_NS}" xmlns:txn="{TXN_NS}"><soapenv:Header>'
        f"<txn:ClientRequestId>{client_id}</txn:ClientRequestId></soapenv:Header><soapenv:Body>"
        f"<txn:Transaction><txn:Type>{txn['type']}</txn:Type><txn:Account>{txn['account']}</txn:Account>"
        f"<txn:AmountCents>{txn['amount_cents']}</txn:AmountCents></txn:Transaction></soapenv:Body></soapenv:Envelope>"
    ).encode()


def one_request(args: argparse.Namespace, ctx: ssl.SSLContext | None, keys: dict, channel: str) -> dict:
    """Móvil y web hablan REST/JSON; el canal de tercero (partner) habla SOAP/XML."""
    client_id = "CLI-" + secrets.token_hex(6)
    txn = {
        "type": random.choice(["TRF", "PAY", "BAL"]),  # noqa: S311
        "account": str(random.randint(10**8, 10**9 - 1)),  # noqa: S311
        "amount_cents": random.randint(100, 500_000),  # noqa: S311
    }
    soap = channel == "partner" and not args.rest_only
    base = args.url.rstrip("/")
    if soap:
        url, body = base + "/soap/v1/transactions", soap_body(txn, client_id)
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"urn:obsproto:txn:v1#Submit"'}
    else:
        url, body = base + "/v1/transactions", json.dumps(txn).encode()
        headers = {"Content-Type": "application/json", "X-Client-Id": client_id}
    req = urllib.request.Request(url, data=body, headers={**headers, "X-Api-Key": keys[channel]}, method="POST")  # noqa: S310
    wall, start = time.time(), time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=args.timeout, context=ctx) as resp:  # noqa: S310
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception:  # noqa: BLE001
        status = 0
    return {
        "client_id": client_id,
        "channel": channel,
        "proto": "SOAP" if soap else "REST",
        "status": status,
        "ms": (time.monotonic() - start) * 1000,
        "t": wall,
    }


def percentile(sorted_values: list, p: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * p))]


def check_traces(base: str, token: str, ids: list) -> dict:
    complete = found = 0
    for cid in ids:
        req = urllib.request.Request(  # noqa: S310
            f"{base.rstrip('/')}/trace/{cid}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                data = json.loads(resp.read())
            found += 1
            complete += bool(data.get("complete"))
        except urllib.error.HTTPError:
            pass
    return {"checked": len(ids), "found": found, "complete": complete}


def publish_cloudwatch(args: argparse.Namespace, results: list, summary: dict) -> dict:
    """Serie por ventanas (rps, latencias, errores) + resumen final, con dimensión Scenario.

    Se publica al terminar la corrida para no quitarle CPU a la prueba.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cw_metrics import datum, publish

    sc, w = args.cw_scenario, args.window
    buckets: dict = {}
    for r in results:
        buckets.setdefault(int(r["t"] // w), []).append(r)
    data = []
    for b, rs in sorted(buckets.items()):
        ts = (b + 1) * w
        ok = sorted(r["ms"] for r in rs if r["status"] == 200)
        data.append(datum("Throughput", len(rs) / w, "Count/Second", ts, Scenario=sc))
        data.append(datum("Errors", len(rs) - len(ok), "Count", ts, Scenario=sc))
        if ok:
            data.append(datum("LatencyP50", statistics.median(ok), "Milliseconds", ts, Scenario=sc))
            data.append(datum("LatencyP95", percentile(ok, 0.95), "Milliseconds", ts, Scenario=sc))
            data.append(datum("LatencyP99", percentile(ok, 0.99), "Milliseconds", ts, Scenario=sc))
    end = max(r["t"] for r in results)
    for proto, p in summary.get("byProtocol", {}).items():
        data.append(datum("LatencyP95", p["p95"], "Milliseconds", end, Scenario=sc, Protocol=proto))
    cor = summary.get("correlation") or {}
    if cor.get("completePct") is not None:
        data.append(datum("CompleteTracesPct", cor["completePct"], "Percent", end, Scenario=sc))
    return {"scenario": sc, "datapoints": len(data), "published": publish(data)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://127.0.0.1:8443")
    ap.add_argument("--cacert", default="certs/server.crt", help="certificado/CA para verificar TLS")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--correlator", default="", help="URL del correlacionador para verificar trazas")
    ap.add_argument("--settle", type=float, default=3.0, help="segundos de espera antes de consultar trazas")
    ap.add_argument("--rest-only", action="store_true", help="todos los canales por REST (línea base sin SOAP)")
    ap.add_argument("--cw-scenario", default="", help="publica la serie en CloudWatch con este nombre de escenario")
    ap.add_argument("--window", type=float, default=10.0, help="tamaño de la ventana en segundos para CloudWatch")
    ap.add_argument("--out", default="", help="archivo JSON donde guardar el resumen (p. ej. evidence/run.json)")
    args = ap.parse_args()

    load_dotenv()
    keys = channel_keys()
    if not keys:
        raise SystemExit("No hay API_KEYS en el entorno ni en .env (ejecuta `make setup`).")
    ctx = ssl.create_default_context(cafile=args.cacert) if args.url.startswith("https") else None
    channels = sorted(keys)

    t0 = time.monotonic()
    with ThreadPoolExecutor(args.concurrency) as pool:
        results = list(pool.map(lambda i: one_request(args, ctx, keys, channels[i % len(channels)]), range(args.n)))
    elapsed = time.monotonic() - t0

    ok = [r for r in results if r["status"] == 200]
    lat = sorted(r["ms"] for r in ok)
    summary = {
        "requests": args.n,
        "ok": len(ok),
        "errors": args.n - len(ok),
        "statusCounts": {
            str(s): sum(1 for r in results if r["status"] == s) for s in sorted({r["status"] for r in results})
        },
        "throughputRps": round(args.n / elapsed, 1),
        "byProtocol": {
            proto: {
                "requests": len(rs),
                "ok": sum(1 for r in rs if r["status"] == 200),
                "p50": round(statistics.median(sorted(r["ms"] for r in rs if r["status"] == 200) or [0]), 1),
                "p95": round(percentile(sorted(r["ms"] for r in rs if r["status"] == 200) or [0], 0.95), 1),
            }
            for proto in ("REST", "SOAP")
            if (rs := [r for r in results if r["proto"] == proto])
        },
        "latencyMs": (
            {
                "p50": round(statistics.median(lat), 1),
                "p95": round(percentile(lat, 0.95), 1),
                "p99": round(percentile(lat, 0.99), 1),
            }
            if lat
            else None
        ),
    }
    token = os.environ.get("CORRELATOR_TOKEN", "")
    if args.correlator and token:
        time.sleep(args.settle)
        # Se verifican TODAS las transacciones enviadas (también las que el gateway reportó con error).
        summary["correlation"] = check_traces(
            args.correlator, token, [r["client_id"] for r in results if r["status"] in (200, 500, 502)]
        )
        c = summary["correlation"]
        c["completePct"] = round(100 * c["complete"] / c["checked"], 2) if c["checked"] else None
    if args.cw_scenario:
        summary["cloudwatch"] = publish_cloudwatch(args, results, summary)
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
