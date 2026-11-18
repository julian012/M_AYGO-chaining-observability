"""Capa 2 – Bus de integración (stand-in genérico de un ESB).

Recibe del gateway, genera su ID propio (BUS-...) y llama al core. Registra el par
(ID del gateway → ID propio). No se puede modificar para reutilizar el ID del gateway.
"""

from __future__ import annotations

from obsproto.common import BaseHandler, HttpError, JsonLogger, env, new_id, post_json, serve, timed
from obsproto.schema import validate_txn

CORE_URL = env("CORE_URL", "http://core:8082") + "/txn"
LOG = JsonLogger("bus", env("LOG_FILE", "/var/log/app/bus.log"))


class BusHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/route":
            raise HttpError(404, "not_found")
        self.require_internal()
        gw_id = self.upstream_id("GW")
        body = validate_txn(self.read_json())  # defensa en profundidad: no se confía en la red
        bus_id = new_id("BUS")

        (status, payload), ms = timed(
            lambda: post_json(CORE_URL, body, {"X-Internal-Token": env("INTERNAL_TOKEN"), "X-Upstream-Id": bus_id})
        )
        LOG.event(bus_id, gw_id, status=status, duration_ms=ms)
        self.send_json(200 if status == 200 else 502, payload if status == 200 else {"error": "upstream_error"})


if __name__ == "__main__":
    serve(BusHandler, int(env("PORT", "8081")), tls=False)
