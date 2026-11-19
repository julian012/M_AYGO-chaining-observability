"""Capa 1 – Gateway (stand-in genérico de un gateway de seguridad/API cerrado).

Punto de entrada único. Recibe dos protocolos:
  - REST/JSON (móvil, web):   POST /v1/transactions        ID del cliente en ``X-Client-Id``
  - SOAP 1.1/XML (terceros):  POST /soap/v1/transactions   ID del cliente en ``ClientRequestId``
Autentica (API key por canal), limita la tasa, valida y **transforma siempre a SOAP** hacia el
bus, como hace un gateway de integración cerrado. Su ID propio (GW-...) viaja en ``wsa:MessageID``.

Log: access log de texto con pares clave=valor (no JSON), ver docs/log-formats.md.
"""

from __future__ import annotations

from typing import Optional

from obsproto.common import (
    BaseHandler,
    HttpError,
    RateLimiter,
    TextLogger,
    channel_for,
    env,
    new_id,
    parse_api_keys,
    post_raw,
    serve,
    timed,
    valid_id,
)
from obsproto.schema import validate_txn
from obsproto.wire import (
    SOAP_CONTENT_TYPE,
    parse_soap_request,
    soap_fault,
    soap_is_ok,
    soap_request,
    soap_response,
)

API_KEYS = parse_api_keys(env("API_KEYS", required=True))
BUS_URL = env("BUS_URL", "http://bus:8081") + "/ws/TransactionService"
LIMITER = RateLimiter(rate_per_sec=float(env("RATE_PER_SEC", "200")), burst=int(env("RATE_BURST", "400")))
LOG = TextLogger(env("LOG_FILE", "/var/log/app/gateway.log"), env("LOG_LEVEL", "INFO"))


class GatewayHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method == "POST" and path == "/v1/transactions":
            self._handle("REST")
        elif method == "POST" and path == "/soap/v1/transactions":
            try:
                self._handle("SOAP")
            except HttpError as exc:  # un cliente SOAP espera un Fault, no JSON
                actor = "Client" if exc.status < 500 else "Server"
                self.send_body(exc.status if exc.status < 500 else 500, soap_fault(exc.code, actor), SOAP_CONTENT_TYPE)
        else:
            raise HttpError(404, "not_found")

    def _handle(self, proto: str) -> None:
        channel = channel_for(self.headers.get("X-Api-Key"), API_KEYS)
        if channel is None:
            raise HttpError(401, "unauthorized")
        if not LIMITER.allow(channel):
            raise HttpError(429, "rate_limited")

        client_id: Optional[str]
        if proto == "REST":
            body = self.read_json()
            client_id = self.headers.get("X-Client-Id")
        else:
            body, client_id, _ = parse_soap_request(self.read_raw())
        if client_id is None:
            client_id = new_id("CLI")  # p. ej. un tercero que no envía ID
        elif not valid_id(client_id, "CLI"):
            raise HttpError(400, "invalid_client_id")
        txn = validate_txn(body)
        gw_id = new_id("GW")

        (status, reply), ms = timed(
            lambda: post_raw(
                BUS_URL,
                soap_request(txn, message_id=gw_id),
                SOAP_CONTENT_TYPE,
                {"X-Internal-Token": env("INTERNAL_TOKEN"), "SOAPAction": '"urn:obsproto:txn:v1#Submit"'},
            )
        )
        ok = status == 200 and soap_is_ok(reply)
        LOG.log(
            "INFO",
            f"gateway tid={gw_id} in_id={client_id} ch={channel} proto={proto} "
            f"status={200 if ok else 502} upstream={status} dur_ms={ms}",
        )

        # El cliente solo recibe SU propio ID: los IDs internos no se exponen.
        if proto == "SOAP":
            if ok:
                self.send_body(200, soap_response("OK", client_id), SOAP_CONTENT_TYPE)
            else:
                self.send_body(500, soap_fault("upstream_error"), SOAP_CONTENT_TYPE)
        elif ok:
            self.send_json(200, {"clientId": client_id, "result": "OK"}, {"X-Client-Id": client_id})
        else:
            self.send_json(502, {"clientId": client_id, "error": "upstream_error"}, {"X-Client-Id": client_id})


if __name__ == "__main__":
    serve(GatewayHandler, int(env("PORT", "8443")), tls=env("TLS", "1") == "1")
