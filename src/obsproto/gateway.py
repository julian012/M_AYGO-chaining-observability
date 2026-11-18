"""Capa 1 – Gateway (stand-in genérico de un gateway de seguridad/API).

Punto de entrada único para móvil, web y API de terceros. Responsabilidades:
autenticar (API key por canal), limitar tasa, validar la entrada, generar su ID
propio (GW-...) y registrar el par (ID del cliente → ID propio).
"""

from __future__ import annotations

from obsproto.common import (
    BaseHandler,
    HttpError,
    JsonLogger,
    RateLimiter,
    channel_for,
    env,
    new_id,
    parse_api_keys,
    post_json,
    serve,
    timed,
    valid_id,
)
from obsproto.schema import validate_txn

API_KEYS = parse_api_keys(env("API_KEYS", required=True))
BUS_URL = env("BUS_URL", "http://bus:8081") + "/route"
LIMITER = RateLimiter(rate_per_sec=float(env("RATE_PER_SEC", "200")), burst=int(env("RATE_BURST", "400")))
LOG = JsonLogger("gateway", env("LOG_FILE", "/var/log/app/gateway.log"))


class GatewayHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/v1/transactions":
            raise HttpError(404, "not_found")

        channel = channel_for(self.headers.get("X-Api-Key"), API_KEYS)
        if channel is None:
            raise HttpError(401, "unauthorized")
        if not LIMITER.allow(channel):
            raise HttpError(429, "rate_limited")

        client_id = self.headers.get("X-Client-Id")
        if client_id is None:
            client_id = new_id("CLI")  # p. ej. un tercero que no envía ID
        elif not valid_id(client_id, "CLI"):
            raise HttpError(400, "invalid_client_id")

        body = validate_txn(self.read_json())
        gw_id = new_id("GW")

        (status, _), ms = timed(
            lambda: post_json(BUS_URL, body, {"X-Internal-Token": env("INTERNAL_TOKEN"), "X-Upstream-Id": gw_id})
        )
        LOG.event(gw_id, client_id, channel=channel, status=status, duration_ms=ms)

        # El cliente solo recibe SU propio ID: los IDs internos no se exponen.
        if status == 200:
            self.send_json(200, {"clientId": client_id, "result": "OK"}, {"X-Client-Id": client_id})
        else:
            self.send_json(502, {"clientId": client_id, "error": "upstream_error"}, {"X-Client-Id": client_id})


if __name__ == "__main__":
    serve(GatewayHandler, int(env("PORT", "8443")), tls=env("TLS", "1") == "1")
