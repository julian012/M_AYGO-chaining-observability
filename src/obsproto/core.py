"""Capa 3 – Core legacy (stand-in genérico de un core no modificable).

Solo emite un log de texto plano de ANCHO FIJO (ver docs/log-formats.md); no tiene
instrumentación ni propaga contexto. Permite inyectar latencia y fallos.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone

from obsproto.common import (
    BaseHandler,
    HttpError,
    env,
    format_core_record,
    new_id,
    post_json,
    serve,
)
from obsproto.schema import validate_txn

MODERN_URL = env("MODERN_URL", "http://modern:8083") + "/notify"
LOG_PATH = env("LOG_FILE", "/var/log/app/core.log")
FAULT_RATE = float(env("FAULT_RATE", "0.0"))  # probabilidad 0..1 de fallo simulado
LATENCY_MIN_MS = int(env("LATENCY_MIN_MS", "5"))
LATENCY_MAX_MS = int(env("LATENCY_MAX_MS", "40"))
_LOCK = threading.Lock()
_FH = open(LOG_PATH, "a", buffering=1, encoding="ascii", errors="replace")


class CoreHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/txn":
            raise HttpError(404, "not_found")
        self.require_internal()
        bus_id = self.upstream_id("BUS")
        body = validate_txn(self.read_json())
        core_id = new_id("CORE")
        start = time.monotonic()
        time.sleep(random.uniform(LATENCY_MIN_MS, max(LATENCY_MIN_MS, LATENCY_MAX_MS)) / 1000)  # noqa: S311

        failed = random.random() < FAULT_RATE  # noqa: S311
        if not failed:  # el "código moderno" se invoca después del core
            post_json(
                MODERN_URL,
                {"type": body["type"]},
                {"X-Internal-Token": env("INTERNAL_TOKEN"), "X-Upstream-Id": core_id},
                1.0,
            )

        dur_ms = int((time.monotonic() - start) * 1000)
        record = format_core_record(
            datetime.now(timezone.utc),
            core_id,
            bus_id,
            str(body["type"]),
            int(body["amount_cents"]),  # type: ignore[arg-type]
            "91" if failed else "00",
            dur_ms,
        )
        with _LOCK:
            _FH.write(record + "\n")
        if failed:
            raise HttpError(500, "core_error")
        self.send_json(200, {"status": "OK"})


if __name__ == "__main__":
    serve(CoreHandler, int(env("PORT", "8082")), tls=False)
