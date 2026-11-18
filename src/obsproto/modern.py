"""Capa 4 – Servicio moderno (stand-in de un servicio posterior al core).

Emite logs JSON estructurados con su ID propio (MOD-...) y el ID del core como padre.
"""

from __future__ import annotations

from obsproto.common import BaseHandler, HttpError, JsonLogger, env, new_id, serve, timed

LOG = JsonLogger("modern", env("LOG_FILE", "/var/log/app/modern.log"))


class ModernHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/notify":
            raise HttpError(404, "not_found")
        self.require_internal()
        core_id = self.upstream_id("CORE")
        self.read_json()
        mod_id = new_id("MOD")
        _, ms = timed(lambda: None)
        LOG.event(mod_id, core_id, status=200, duration_ms=ms)
        self.send_json(200, {"ok": True})


if __name__ == "__main__":
    serve(ModernHandler, int(env("PORT", "8083")), tls=False)
