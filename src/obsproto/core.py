"""Capa 3 – Core legacy (stand-in genérico de un core COBOL no modificable).

Entrada: **trama de ancho fijo** (``POST /trama``); el ID del bus viene en una posición de la trama,
no en un header. Salida: trama de respuesta con código ``rc``.
Log: solo un registro de texto plano de ANCHO FIJO (ver docs/log-formats.md), sin niveles,
sin nombres de campo y sin contexto de traza. Permite inyectar latencia y fallos.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone

from obsproto.common import BaseHandler, HttpError, env, format_core_record, new_id, post_json, serve
from obsproto.schema import validate_txn
from obsproto.wire import TRAMA_CONTENT_TYPE, parse_trama_request, trama_response

MODERN_URL = env("MODERN_URL", "http://modern:8083") + "/notify"
LOG_PATH = env("LOG_FILE", "/var/log/app/core.log")
FAULT_RATE = float(env("FAULT_RATE", "0.0"))  # probabilidad 0..1 de fallo simulado
LATENCY_MIN_MS = int(env("LATENCY_MIN_MS", "5"))
LATENCY_MAX_MS = int(env("LATENCY_MAX_MS", "40"))
_LOCK = threading.Lock()
_FH = open(LOG_PATH, "a", buffering=1, encoding="ascii", errors="replace")


class CoreHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/trama":
            raise HttpError(404, "not_found")
        self.require_internal()
        bus_id, body = parse_trama_request(self.read_raw())
        txn = validate_txn(body)
        core_id = new_id("CORE")
        start = time.monotonic()
        time.sleep(random.uniform(LATENCY_MIN_MS, max(LATENCY_MIN_MS, LATENCY_MAX_MS)) / 1000)  # noqa: S311

        failed = random.random() < FAULT_RATE  # noqa: S311
        if not failed:  # el "código moderno" se invoca después del core
            post_json(
                MODERN_URL,
                {"type": txn["type"]},
                {"X-Internal-Token": env("INTERNAL_TOKEN"), "X-Upstream-Id": core_id},
                1.0,
            )

        dur_ms = int((time.monotonic() - start) * 1000)
        record = format_core_record(
            datetime.now(timezone.utc),
            core_id,
            bus_id,
            str(txn["type"]),
            int(txn["amount_cents"]),  # type: ignore[arg-type]
            "91" if failed else "00",
            dur_ms,
        )
        with _LOCK:
            _FH.write(record + "\n")
        # Como un core real, responde por la trama (rc), no con un código HTTP de error.
        self.send_body(200, trama_response(bus_id, "91" if failed else "00"), TRAMA_CONTENT_TYPE)


if __name__ == "__main__":
    serve(CoreHandler, int(env("PORT", "8082")), tls=False)
