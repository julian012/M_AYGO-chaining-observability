"""Capa 2 – Bus de integración (stand-in genérico de un ESB).

Recibe SOAP del gateway (ID del gateway en ``wsa:MessageID``), genera su ID propio (BUS-...)
y llama al core con una **trama de ancho fijo** (el ID del bus viaja en las posiciones 4-23).

Log: texto con niveles, como un ESB real. El par (ID recibido -> ID propio) solo queda
completo en una línea DEBUG; en INFO aparecen por separado la recepción (con el ID del
gateway) y la respuesta (con el ID propio), y nada las une de forma determinista.
Con ``LOG_LEVEL=INFO`` la cadena se rompe en el bus: es justo el escenario que se quiere medir.
"""

from __future__ import annotations

import threading

from obsproto.common import BaseHandler, HttpError, TextLogger, env, new_id, post_raw, serve, timed, valid_id
from obsproto.schema import validate_txn
from obsproto.wire import (
    SOAP_CONTENT_TYPE,
    TRAMA_CONTENT_TYPE,
    parse_soap_request,
    parse_trama_response,
    soap_fault,
    soap_response,
    trama_request,
)

CORE_URL = env("CORE_URL", "http://core:8082") + "/trama"
LOG = TextLogger(env("LOG_FILE", "/var/log/app/bus.log"), env("LOG_LEVEL", "DEBUG"))
FLOW = "[ESB.TransactionRoute]"


class BusHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "POST" or path != "/ws/TransactionService":
            raise HttpError(404, "not_found")
        try:
            self.require_internal()
            body, _, gw_id = parse_soap_request(self.read_raw())
            if not valid_id(gw_id, "GW"):
                raise HttpError(400, "invalid_message_id")
            txn = validate_txn(body)  # defensa en profundidad: no se confía en la red
        except HttpError as exc:
            self.send_body(exc.status if exc.status < 500 else 500, soap_fault(exc.code, "Client"), SOAP_CONTENT_TYPE)
            return

        thread = f"T{threading.get_ident() % 100000:05d}"  # como el hilo de un flujo ESB
        LOG.log("INFO", f"{FLOW} thread={thread} received SOAP Submit upstream={gw_id}")
        bus_id = new_id("BUS")
        LOG.log("DEBUG", f"{FLOW} thread={thread} mapped msgId={bus_id} upstream={gw_id} out=CORE.TRAMA")

        (status, raw), ms = timed(
            lambda: post_raw(
                CORE_URL, trama_request(bus_id, txn), TRAMA_CONTENT_TYPE, {"X-Internal-Token": env("INTERNAL_TOKEN")}
            )
        )
        rc = parse_trama_response(raw) if status == 200 else None
        code = rc if rc is not None else ("TO" if status == 503 else "XX")  # TO: timeout/caída · XX: respuesta inválida
        LOG.log(
            "INFO" if code == "00" else "ERROR", f"{FLOW} thread={thread} reply msgId={bus_id} rc={code} dur_ms={ms}"
        )

        if code == "00":
            self.send_body(200, soap_response("OK"), SOAP_CONTENT_TYPE)
        else:
            self.send_body(500, soap_fault(f"core_rc_{code}"), SOAP_CONTENT_TYPE)


if __name__ == "__main__":
    serve(BusHandler, int(env("PORT", "8081")), tls=False)
