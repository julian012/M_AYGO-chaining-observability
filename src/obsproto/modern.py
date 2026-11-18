"""Capa 4 – Servicio moderno (stand-in de un servicio posterior al core, instrumentado con OTel).

Entrada: REST/JSON con el ID del core en ``X-Upstream-Id``.
Telemetría: spans al estilo OpenTelemetry (no un log de texto). Como el core no propaga
``traceparent``, cada span abre una traza OTel nueva; el vínculo con el resto del flujo
solo existe en los atributos ``app.id`` (MOD-...) y ``upstream.id`` (CORE-...).
"""

from __future__ import annotations

import time

from obsproto.common import BaseHandler, HttpError, SpanExporter, env, new_id, serve

SPANS = SpanExporter("modern-notifications", env("LOG_FILE", "/var/log/app/modern.log"))


class ModernHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/notify":
            raise HttpError(404, "not_found")
        start_ns = time.time_ns()
        self.require_internal()
        core_id = self.upstream_id("CORE")
        self.read_json()
        mod_id = new_id("MOD")
        self.send_json(200, {"ok": True})
        SPANS.export(
            "POST /notify",
            start_ns,
            time.time_ns(),
            True,
            {"app.id": mod_id, "upstream.id": core_id, "http.request.method": "POST", "http.response.status_code": 200},
        )


if __name__ == "__main__":
    serve(ModernHandler, int(env("PORT", "8083")), tls=False)
