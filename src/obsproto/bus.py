"""Capa 2 – Bus de integración (stand-in genérico de un ESB).

Recibe SOAP del gateway (ID del gateway en ``wsa:MessageID``), genera su ID propio (BUS-...)
y llama al core con una **trama de ancho fijo por un socket TCP** (cabecera de longitud de 4 dígitos);
el ID del bus viaja en las posiciones 4-23, o no viaja si el core es tipo Stratus.

Log: texto con niveles, como un ESB real. El par (ID recibido -> ID propio) solo queda
completo en una línea DEBUG; en INFO aparecen por separado la recepción (con el ID del
gateway) y la respuesta (con el ID propio), y nada las une de forma determinista.
Con ``LOG_LEVEL=INFO`` la cadena se rompe en el bus: es justo el escenario que se quiere medir.
Con ``BUS_INFO_PROFILE=summary`` el bus deja una sola línea INFO por mensaje con su ID, el recibido y,
si el core es tipo Stratus (``CORE_LINK=tracenumber``), el tracenumber que devolvió según ``BUS_TRACE_LOG``
(claro, token, máscara o nada). Ver ``obsproto/tracelink.py``.
"""

from __future__ import annotations

import sys
import threading

from obsproto.common import BaseHandler, HttpError, TextLogger, env, new_id, serve, timed, valid_id
from obsproto.schema import validate_txn
from obsproto.tracelink import POLICIES, obfuscate
from obsproto.wire import (
    SOAP_CONTENT_TYPE,
    parse_soap_request,
    parse_trama_response,
    soap_fault,
    soap_response,
    tcp_exchange,
    trama_request,
)

CORE_HOST, _, _port = env("CORE_ADDR", "core:9000").partition(":")
CORE_PORT = int(_port or 9000)
LOG = TextLogger(env("LOG_FILE", "/var/log/app/bus.log"), env("LOG_LEVEL", "DEBUG"))
FLOW = "[ESB.TransactionRoute]"
PROFILE = env("BUS_INFO_PROFILE", "split")  # split: par solo en DEBUG · summary: una línea INFO por mensaje
CORE_LINK = env("CORE_LINK", "frame")  # frame: el ID va en la trama · tracenumber: el core no lo recibe
TRACE_POLICY = env("BUS_TRACE_LOG", "clear")
TRACE_VISIBLE = int(env("BUS_TRACE_MASK_DIGITS", "4"))
TRACE_KEY = env("TRACE_TOKEN_KEY", "")
if TRACE_POLICY not in POLICIES or (CORE_LINK == "tracenumber" and TRACE_POLICY == "token" and not TRACE_KEY):
    sys.exit("[config] BUS_TRACE_LOG inválido, o 'token' sin TRACE_TOKEN_KEY")
# Verdad de referencia SOLO para evaluar la unión (enlaces falsos). El correlacionador nunca lee este archivo.
_TRUTH = open(env("TRUTH_FILE", "/var/log/app/truth.log"), "a", buffering=1) if CORE_LINK == "tracenumber" else None
_TRUTH_LOCK = threading.Lock()


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
        bus_id = new_id("BUS")
        if PROFILE == "split":
            LOG.log("INFO", f"{FLOW} thread={thread} received SOAP Submit upstream={gw_id}")
            LOG.log("DEBUG", f"{FLOW} thread={thread} mapped msgId={bus_id} upstream={gw_id} out=CORE.TRAMA")

        frame = trama_request(bus_id, txn, include_id=CORE_LINK != "tracenumber")
        raw, ms = timed(lambda: tcp_exchange(CORE_HOST, CORE_PORT, frame, timeout=3.0))  # socket TCP, no HTTP
        parsed = parse_trama_response(raw) if raw is not None else None
        rc, tracenumber = parsed if parsed else (None, None)
        code = rc if rc is not None else ("TO" if raw is None else "XX")  # TO: timeout/caída · XX: respuesta inválida

        fields = f"reply msgId={bus_id}"
        if PROFILE == "summary":
            fields += f" upstream={gw_id}"  # resumen por mensaje: el par queda en INFO
        fields += f" rc={code}"
        if tracenumber:
            shown = obfuscate(tracenumber, TRACE_POLICY, TRACE_KEY, TRACE_VISIBLE)
            if shown:
                fields += f" trace={shown}"
            if _TRUTH is not None:
                with _TRUTH_LOCK:
                    _TRUTH.write(f"{bus_id} CORE-{tracenumber}\n")
        LOG.log("INFO" if code == "00" else "ERROR", f"{FLOW} thread={thread} {fields} dur_ms={ms}")

        if code == "00":
            self.send_body(200, soap_response("OK"), SOAP_CONTENT_TYPE)
        else:
            self.send_body(500, soap_fault(f"core_rc_{code}"), SOAP_CONTENT_TYPE)


if __name__ == "__main__":
    serve(BusHandler, int(env("PORT", "8081")), tls=False)
