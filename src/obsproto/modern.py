"""Capa 4 – Servicio moderno (stand-in de un servicio posterior al core, instrumentado con OTel).

Entrada: **archivos planos** de novedades que el core deja por **FTP** en ``/inbox`` (no hay llamada en línea).
Cada ``POLL_S`` segundos revisa el FTP, descarga los ``.txt``, valida cabecera, detalle y cola (si el conteo no
cuadra, el archivo se aparta como ``.err`` y no se procesa) y, al terminar, lo mueve a ``/procesados``.

Telemetría: un span al estilo OpenTelemetry por cada registro procesado. Como el core no propaga ``traceparent``,
cada span abre una traza OTel nueva; el vínculo con el resto del flujo está en los atributos ``app.id`` (MOD-...)
y ``upstream.id`` (el ID del core que viene en el registro). El tramo core -> moderno es asíncrono: el span llega
segundos después de que la transacción ya terminó.
"""

from __future__ import annotations

import ftplib
import io
import sys
import threading
import time

from obsproto.common import BaseHandler, HttpError, SpanExporter, env, new_id, serve
from obsproto.wire import parse_novedades

SPANS = SpanExporter("modern-notifications", env("LOG_FILE", "/var/log/app/modern.log"))
FTP_HOST, FTP_PORT = env("FTP_HOST", "ftp"), int(env("FTP_PORT", "2121"))
POLL_S = float(env("POLL_S", "2"))


def process_file(name: str, text: str) -> int:
    """Emite un span por novedad. Devuelve cuántas procesó, o -1 si el archivo no es íntegro."""
    details = parse_novedades(text)
    if details is None:
        return -1
    for d in details:
        start = time.time_ns()
        mod_id = new_id("MOD")
        SPANS.export(
            "process NOVEDAD",
            start,
            time.time_ns(),
            True,
            {"app.id": mod_id, "upstream.id": d["core_id"], "file.name": name, "file.records": len(details)},
        )
    return len(details)


def poll_ftp(stop: threading.Event) -> None:
    while not stop.wait(POLL_S):
        try:
            with ftplib.FTP() as ftp:  # noqa: S321 - FTP en la red interna; en producción SFTP/FTPS
                ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
                ftp.login(env("FTP_USER", "novedades"), env("FTP_PASSWORD", required=True))
                for path in sorted(ftp.nlst("inbox")):
                    name = path.rsplit("/", 1)[-1]
                    if not name.endswith(".txt"):
                        continue  # .tmp: el core todavía lo está subiendo
                    buf = io.BytesIO()
                    ftp.retrbinary(f"RETR inbox/{name}", buf.write)
                    n = process_file(name, buf.getvalue().decode("ascii", "replace"))
                    ftp.rename(f"inbox/{name}", f"procesados/{name}" + (".err" if n < 0 else ""))
                    print(f"[modern] {name}: {n if n >= 0 else 'archivo no íntegro'} novedades", flush=True)
        except (OSError, ftplib.Error) as exc:
            print(f"[modern] FTP no disponible: {exc}", file=sys.stderr, flush=True)


class ModernHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        raise HttpError(404, "not_found")  # solo /healthz (en BaseHandler): el trabajo llega por FTP


if __name__ == "__main__":
    env("FTP_PASSWORD", required=True)
    threading.Thread(target=poll_ftp, args=(threading.Event(),), daemon=True).start()
    serve(ModernHandler, int(env("PORT", "8083")), tls=False)
