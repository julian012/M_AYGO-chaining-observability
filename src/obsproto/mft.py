"""Plataforma de transferencia de archivos (MFT, stand-in genérico de un manejador de archivos).

Mueve los archivos planos de novedades del lado del core (``/outbox``) al lado del servicio moderno (``/inbox``).
Cada ``MFT_POLL_S`` segundos ejecuta el job ``NOVEDADES_<nodo>``: abre una sesión, lista ``/outbox``, y por cada
``.txt`` lo **copia** (descarga, sube a ``/inbox`` como ``.tmp``, verifica el hash y lo renombra) y lo **mueve**
(borra el origen). No abre ni interpreta el contenido: solo conoce el nombre, el tamaño y el hash.

Log: texto con la estructura de una plataforma MFT (una línea por evento, con job, ejecución, sesión y transferencia):
  <ts> INFO  [MFT] job=NOVEDADES_CORE01 run=RUN-… session=SSH-… event=ssh.connect host=ftp user=mft
  <ts> INFO  [MFT] job=… run=… session=… transfer=MFT-… event=file.copy src=/outbox/NOV_… dst=/inbox/NOV_…
       bytes=… sha256=…
  <ts> INFO  [MFT] job=… run=… session=… transfer=MFT-… event=file.move src=/outbox/NOV_… status=OK
  <ts> INFO  [MFT] job=… run=… session=… event=ssh.disconnect files=1

Nota de honestidad (ADR-0008): una plataforma MFT real usaría SFTP sobre SSH. La biblioteca estándar no trae SSH,
así que el stand-in mueve los archivos por el FTP interno; lo que se simula fielmente es su log de sesión y de archivo,
que es lo único que usa el correlacionador.
"""

from __future__ import annotations

import ftplib
import hashlib
import io
import sys
import threading

from obsproto.common import BaseHandler, HttpError, TextLogger, env, new_id, serve

LOG = TextLogger(env("LOG_FILE", "/var/log/app/mft.log"), "INFO")
FTP_HOST, FTP_PORT = env("FTP_HOST", "ftp"), int(env("FTP_PORT", "2121"))
POLL_S = float(env("MFT_POLL_S", "3"))
JOB = f"NOVEDADES_{env('CORE_NODE', 'CORE01')}"
TAG = "[MFT]"


def run_once() -> int:
    """Una ejecución del job. Devuelve cuántos archivos movió."""
    run, session = new_id("RUN"), new_id("SSH")
    ctx = f"{TAG} job={JOB} run={run} session={session}"
    moved = 0
    with ftplib.FTP() as ftp:  # noqa: S321 - red interna; una MFT real usaría SFTP (ver docstring)
        ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
        ftp.login(env("FTP_USER", "novedades"), env("FTP_PASSWORD", required=True))
        names = [p.rsplit("/", 1)[-1] for p in ftp.nlst("outbox")]
        names = [n for n in sorted(names) if n.endswith(".txt")]  # .tmp: el core todavía lo está subiendo
        if not names:
            return 0
        LOG.log("INFO", f"{ctx} event=ssh.connect host={FTP_HOST} user=mft")
        for name in names:
            transfer = new_id("MFT")
            buf = io.BytesIO()
            ftp.retrbinary(f"RETR outbox/{name}", buf.write)
            data = buf.getvalue()
            digest = hashlib.sha256(data).hexdigest()[:16]
            ftp.storbinary(f"STOR inbox/{name}.tmp", io.BytesIO(data))
            check = io.BytesIO()
            ftp.retrbinary(f"RETR inbox/{name}.tmp", check.write)
            if hashlib.sha256(check.getvalue()).hexdigest()[:16] != digest:  # verificación de integridad
                LOG.log("ERROR", f"{ctx} transfer={transfer} event=file.copy src=/outbox/{name} status=HASH_MISMATCH")
                ftp.delete(f"inbox/{name}.tmp")
                continue
            ftp.rename(f"inbox/{name}.tmp", f"inbox/{name}")
            LOG.log(
                "INFO",
                f"{ctx} transfer={transfer} event=file.copy src=/outbox/{name} dst=/inbox/{name} "
                f"bytes={len(data)} sha256={digest}",
            )
            ftp.delete(f"outbox/{name}")
            LOG.log("INFO", f"{ctx} transfer={transfer} event=file.move src=/outbox/{name} status=OK")
            moved += 1
        LOG.log("INFO", f"{ctx} event=ssh.disconnect files={moved}")
    return moved


def loop(stop: threading.Event) -> None:
    while not stop.wait(POLL_S):
        try:
            run_once()
        except (OSError, ftplib.Error) as exc:
            print(f"[mft] destino no disponible: {exc}", file=sys.stderr, flush=True)


class MftHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        raise HttpError(404, "not_found")  # solo /healthz


if __name__ == "__main__":
    env("FTP_PASSWORD", required=True)
    threading.Thread(target=loop, args=(threading.Event(),), daemon=True).start()
    serve(MftHandler, int(env("PORT", "8084")), tls=False)
