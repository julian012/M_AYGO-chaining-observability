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
import itertools
import json
import os
import random
import secrets
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
    """Cuántas trazas existen y están completas; para las incompletas, qué capas faltan."""
    complete = found = unreachable = 0
    missing: dict = {}
    for cid in ids:
        req = urllib.request.Request(  # noqa: S310
            f"{base.rstrip('/')}/trace/{cid}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.HTTPError:
            continue  # 404: el correlacionador no conoce ese ID
        except (urllib.error.URLError, OSError):
            unreachable += 1
            continue
        found += 1
        if data.get("complete"):
            complete += 1
        else:
            key = "+".join(data.get("missingLayers") or []) or "?"
            missing[key] = missing.get(key, 0) + 1
    out = {"checked": len(ids), "found": found, "complete": complete, "incompleteByMissingLayers": missing}
    if unreachable:
        out["unreachable"] = unreachable
    return out


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


def run_load(args: argparse.Namespace, ctx: ssl.SSLContext | None, keys: dict) -> tuple:
    """Por cantidad (--n) o por tiempo (--duration). Devuelve (resultados, segundos transcurridos)."""
    channels = sorted(keys)
    t0 = time.monotonic()
    if args.duration > 0:
        deadline = t0 + args.duration
        results: list = []
        lock = threading.Lock()
        counter = itertools.count()

        def worker() -> None:
            local = []
            while time.monotonic() < deadline:
                local.append(one_request(args, ctx, keys, channels[next(counter) % len(channels)]))
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(args.concurrency)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:
        with ThreadPoolExecutor(args.concurrency) as pool:
            results = list(pool.map(lambda i: one_request(args, ctx, keys, channels[i % len(channels)]), range(args.n)))
    return results, time.monotonic() - t0


def summarize(args: argparse.Namespace, results: list, elapsed: float) -> dict:
    ok = [r for r in results if r["status"] == 200]
    lat = sorted(r["ms"] for r in ok)

    def proto_stats(rs: list) -> dict:
        ms = sorted(r["ms"] for r in rs if r["status"] == 200) or [0]
        return {
            "requests": len(rs),
            "ok": sum(1 for r in rs if r["status"] == 200),
            "p50": round(statistics.median(ms), 1),
            "p95": round(percentile(ms, 0.95), 1),
        }

    return {
        "scenario": args.cw_scenario or None,
        "startedAt": datetime.fromtimestamp(min(r["t"] for r in results), timezone.utc).isoformat(timespec="seconds"),
        "params": {
            "n": len(results),
            "concurrency": args.concurrency,
            "duration": args.duration or None,
            "restOnly": args.rest_only,
            "url": args.url,
        },
        "requests": len(results),
        "ok": len(ok),
        "errors": len(results) - len(ok),
        "statusCounts": {
            str(s): sum(1 for r in results if r["status"] == s) for s in sorted({r["status"] for r in results})
        },
        "elapsedSec": round(elapsed, 1),
        "throughputRps": round(len(results) / elapsed, 1),
        "byProtocol": {
            proto: proto_stats(rs) for proto in ("REST", "SOAP") if (rs := [r for r in results if r["proto"] == proto])
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


def verify(args: argparse.Namespace, ids: list) -> dict:
    """Consulta /trace por cada ID. Con --verify-last solo las N más recientes (el grafo tiene evicción FIFO)."""
    token = os.environ.get("CORRELATOR_TOKEN", "")
    if not (args.correlator and token):
        return {}
    sample = ids[-args.verify_last :] if args.verify_last else ids
    c = check_traces(args.correlator, token, sample)
    c["completePct"] = round(100 * c["complete"] / c["checked"], 2) if c["checked"] else None
    c["sampled"] = f"last {len(sample)} of {len(ids)}" if args.verify_last else "all"
    return c


def publish_completeness(args: argparse.Namespace, summary: dict, end_ts: float) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cw_metrics import datum, publish

    pct = (summary.get("correlation") or {}).get("completePct")
    if args.cw_scenario and pct is not None:
        publish([datum("CompleteTracesPct", pct, "Percent", end_ts, Scenario=args.cw_scenario)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://127.0.0.1:8443")
    ap.add_argument("--cacert", default="certs/server.crt", help="certificado/CA para verificar TLS")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--duration", type=float, default=0, help="segundos de carga sostenida (ignora --n)")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--correlator", default="", help="URL del correlacionador para verificar trazas")
    ap.add_argument(
        "--settle",
        type=float,
        default=12.0,
        help="espera antes de consultar trazas: el tramo core -> moderno llega por lotes FTP (5 s + 2 s)",
    )
    ap.add_argument("--verify-last", type=int, default=0, help="verificar solo las N transacciones más recientes")
    ap.add_argument("--rest-only", action="store_true", help="todos los canales por REST (línea base sin SOAP)")
    ap.add_argument("--cw-scenario", default="", help="publica la serie en CloudWatch con este nombre de escenario")
    ap.add_argument("--window", type=float, default=10.0, help="tamaño de la ventana en segundos para CloudWatch")
    ap.add_argument("--ids-out", default="", help="guarda los IDs enviados para verificarlos desde otra máquina")
    ap.add_argument("--verify-ids", default="", help="solo verificar: IDs de una corrida hecha en otra máquina")
    ap.add_argument(
        "--summary", default="", help="con --verify-ids: resumen de esa corrida, al que se agrega la verificación"
    )
    ap.add_argument("--out", default="", help="archivo JSON donde guardar el resumen (p. ej. evidence/run.json)")
    args = ap.parse_args()
    load_dotenv()

    if args.verify_ids:  # modo verificación (en la instancia de la aplicación, junto al correlacionador)
        saved = json.loads(Path(args.verify_ids).read_text())
        summary = json.loads(Path(args.summary).read_text()) if args.summary else {}
        time.sleep(args.settle)
        summary["correlation"] = verify(args, saved["ids"])
        publish_completeness(args, summary, saved["end"])
    else:
        keys = channel_keys()
        if not keys:
            raise SystemExit("No hay API_KEYS en el entorno ni en .env (ejecuta `make setup`).")
        ctx = ssl.create_default_context(cafile=args.cacert) if args.url.startswith("https") else None
        results, elapsed = run_load(args, ctx, keys)
        summary = summarize(args, results, elapsed)
        # Se verifican TODAS las transacciones enviadas (también las que el gateway reportó con error).
        ids = [r["client_id"] for r in sorted(results, key=lambda r: r["t"]) if r["status"] in (200, 500, 502)]
        end = max(r["t"] for r in results)
        if args.ids_out:
            Path(args.ids_out).write_text(json.dumps({"ids": ids, "end": end}))
        if args.correlator:
            time.sleep(args.settle)
            summary["correlation"] = verify(args, ids)
        if args.cw_scenario:
            summary["cloudwatch"] = publish_cloudwatch(args, results, summary)

    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
