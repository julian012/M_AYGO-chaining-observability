"""Capa 3 – Core legacy (stand-in genérico de un core legado no modificable que solo se integra por tramas).

Entrada: **tramas por un socket TCP** (no HTTP). Cada mensaje lleva una cabecera de 4 dígitos con su longitud;
la conexión puede traer varios mensajes seguidos. Dos modos (``CORE_LINK``):
  - ``frame``: el ID del bus viene en las posiciones 4-23 y el core lo registra como "ID recibido".
  - ``tracenumber``: NO recibe el ID del bus. Asigna a cada movimiento un consecutivo que nunca se repite
    (el tracenumber), lo registra como su ID y lo devuelve en la respuesta.
Salida: trama de respuesta con el código ``rc`` y el tracenumber.

Hacia el servicio moderno no hay llamada en línea: el core acumula las **novedades** de los movimientos exitosos y
cada ``BATCH_INTERVAL_S`` segundos las deja en un **archivo plano** de ancho fijo (cabecera, detalle y cola con el
conteo) que sube por **FTP** a ``/outbox`` (primero como ``.tmp`` y luego lo renombra, para que nunca se lea a medias).
El nombre dice el nodo, la fecha y el rango de horas de los movimientos:
``NOV_CORE01_20261119_072059670-072104512.txt``.
Una plataforma MFT (``obsproto/mft.py`` lo lleva de ``/outbox`` a ``/inbox`` del moderno.

Log: un registro de texto plano de ANCHO FIJO (ver docs/log-formats.md), sin niveles ni contexto de traza.
Permite inyectar latencia y fallos.
"""

from __future__ import annotations

import ftplib
import io
import itertools
import random
import signal
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from typing import List

from obsproto.common import HttpError, chaos, env, format_core_record, new_id
from obsproto.schema import validate_txn
from obsproto.wire import (
    novedad_detail,
    novedad_filename,
    novedad_header,
    novedad_trailer,
    parse_trama_request,
    tcp_frame,
    tcp_read_frame,
    trama_response,
)

LOG_PATH = env("LOG_FILE", "/var/log/app/core.log")
FAULT_RATE = float(env("FAULT_RATE", "0.0"))  # probabilidad 0..1 de fallo simulado
LATENCY_MIN_MS = int(env("LATENCY_MIN_MS", "5"))
LATENCY_MAX_MS = int(env("LATENCY_MAX_MS", "40"))
CORE_LINK = env("CORE_LINK", "frame")
NODE = env("CORE_NODE", "CORE01")
BATCH_INTERVAL_S = float(env("BATCH_INTERVAL_S", "5"))
FTP_HOST, FTP_PORT = env("FTP_HOST", "ftp"), int(env("FTP_PORT", "2121"))
_LOCK = threading.Lock()
# Consecutivo que no se repite ni tras un reinicio: arranca en el reloj en ms (12 dígitos) y suma 1 por movimiento.
_TRACE = itertools.count(int(time.time() * 1000) % 10**12)
_FH = open(LOG_PATH, "a", buffering=1, encoding="ascii", errors="replace")
_NOVEDADES: List[str] = []  # registros de detalle pendientes de enviar al moderno


def _stamp(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") + f"{ts.microsecond // 1000:03d}"


def process(frame: bytes) -> bytes:
    """Una trama de solicitud -> una trama de respuesta (el corazón del core)."""
    try:
        bus_id, body = parse_trama_request(frame, allow_blank_id=CORE_LINK == "tracenumber")
        if CORE_LINK != "tracenumber" and bus_id is None:
            raise HttpError(400, "invalid_trama")
        txn = validate_txn(body)
    except HttpError:
        return trama_response(None, "30")  # 30 = error de formato
    tracenumber = 0
    if CORE_LINK == "tracenumber":
        with _LOCK:
            tracenumber = next(_TRACE)
        core_id = f"CORE-{tracenumber:012d}"  # el tracenumber ES el ID del core
    else:
        core_id = new_id("CORE")
    start = time.monotonic()
    extra = chaos().get("extra_latency_ms", 0)  # degradación inyectada en caliente (experimentos)
    extra = extra if isinstance(extra, (int, float)) and 0 <= extra <= 5000 else 0
    time.sleep((random.uniform(LATENCY_MIN_MS, max(LATENCY_MIN_MS, LATENCY_MAX_MS)) + extra) / 1000)  # noqa: S311
    failed = random.random() < FAULT_RATE  # noqa: S311
    status = "91" if failed else "00"
    now = datetime.now(timezone.utc)
    record = format_core_record(
        now,
        core_id,
        bus_id or "",  # en modo tracenumber el core no sabe quién lo llamó
        str(txn["type"]),
        int(txn["amount_cents"]),  # type: ignore[arg-type]
        status,
        int((time.monotonic() - start) * 1000),
    )
    with _LOCK:
        _FH.write(record + "\n")
        if not failed:  # solo los movimientos exitosos generan novedad para el moderno
            _NOVEDADES.append(novedad_detail(core_id, str(txn["type"]), int(txn["amount_cents"]), status, _stamp(now)))  # type: ignore[arg-type]
    return trama_response(bus_id, status, tracenumber)


class CoreTcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(10)  # corta conexiones lentas
        while True:
            try:
                frame = tcp_read_frame(self.request)
            except (OSError, ValueError):
                return  # trama mal formada o conexión rota: se cierra sin responder
            if frame is None:
                return
            self.request.sendall(tcp_frame(process(frame)))


def ship_batches(stop: threading.Event) -> None:
    """Cada BATCH_INTERVAL_S sube por FTP un archivo plano con las novedades acumuladas."""
    while not stop.wait(BATCH_INTERVAL_S):
        with _LOCK:
            batch = _NOVEDADES[:]
            _NOVEDADES.clear()
        if not batch:
            continue
        stamps = [rec[40:57] for rec in batch]  # hora del movimiento (posiciones 40-56 del detalle)
        if stamps[0][:8] != stamps[-1][:8]:  # el lote cruzó la medianoche: se parte en dos días
            day = stamps[0][:8]
            with _LOCK:
                _NOVEDADES[:0] = [r for r in batch if r[40:48] != day]
            batch = [r for r in batch if r[40:48] == day]
            stamps = [rec[40:57] for rec in batch]
        name = novedad_filename(NODE, min(stamps), max(stamps))[:-4]
        content = (
            "\n".join([novedad_header(_stamp(datetime.now(timezone.utc)), NODE), *batch, novedad_trailer(len(batch))])
            + "\n"
        )
        try:
            with ftplib.FTP() as ftp:  # noqa: S321 - FTP en la red interna; en producción SFTP/FTPS
                ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
                ftp.login(env("FTP_USER", "novedades"), env("FTP_PASSWORD", required=True))
                ftp.storbinary(f"STOR outbox/{name}.tmp", io.BytesIO(content.encode("ascii")))
                ftp.rename(f"outbox/{name}.tmp", f"outbox/{name}.txt")
            print(f"[core] lote {name}.txt enviado por FTP ({len(batch)} novedades)", flush=True)
        except (OSError, ftplib.Error) as exc:
            with _LOCK:
                _NOVEDADES[:0] = batch  # se reintenta en el siguiente lote
            print(f"[core] FTP no disponible, se reintenta: {exc}", file=sys.stderr, flush=True)


class CoreServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def main() -> None:
    env("FTP_PASSWORD", required=True)
    stop = threading.Event()
    threading.Thread(target=ship_batches, args=(stop,), daemon=True).start()
    server = CoreServer(("0.0.0.0", int(env("PORT", "9000"))), CoreTcpHandler)  # noqa: S104 - dentro del contenedor

    def _stop(*_: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(
        f"[core] tramas por TCP en :{server.server_address[1]} · modo {CORE_LINK} · lotes cada {BATCH_INTERVAL_S}s",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
