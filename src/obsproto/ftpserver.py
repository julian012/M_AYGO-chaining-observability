"""Servidor FTP mínimo (stand-in) para la transferencia de archivos planos core -> moderno.

Solo implementa lo que usan el core y el servicio moderno con ``ftplib``: USER/PASS, TYPE, PWD, CWD, PASV,
STOR, RETR, NLST, RNFR/RNTO, DELE, NOOP, QUIT. Solo biblioteca estándar (ADR-0003).

Seguridad (ver docs/threat-model.md): usuario y contraseña por variables de entorno (comparación en tiempo
constante), solo dos carpetas (``/inbox`` y ``/procesados``), nombres de archivo en lista blanca (sin ``..``
ni rutas), tamaño máximo por archivo y conexiones con timeout. Como cualquier FTP, viaja en claro: solo
escucha en la red interna de Docker. En producción: SFTP o FTPS.
"""

from __future__ import annotations

import hmac
import os
import re
import signal
import socket
import socketserver
import sys
import threading
from pathlib import Path
from typing import Optional

DIRS = ("inbox", "procesados")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\Z")
MAX_FILE = 10 * 2**20
ROOT = Path(os.environ.get("FTP_ROOT", "/srv/ftp"))
USER = os.environ.get("FTP_USER", "novedades")
PASSWORD = os.environ.get("FTP_PASSWORD", "")


class FtpHandler(socketserver.StreamRequestHandler):
    timeout = 60

    def setup(self) -> None:
        super().setup()
        self.user_ok = self.authed = False
        self.cwd = "/"
        self.pasv: Optional[socket.socket] = None
        self.rnfr: Optional[Path] = None

    def reply(self, text: str) -> None:
        self.wfile.write((text + "\r\n").encode())

    # -- rutas ------------------------------------------------------------------------------------- #
    def _dir(self, arg: str) -> Optional[str]:
        target = arg.strip("/") if arg.startswith("/") else (self.cwd.strip("/") + "/" + arg).strip("/")
        return target if target in ("",) + DIRS else None

    def _file(self, arg: str) -> Optional[Path]:
        parts = (arg if arg.startswith("/") else self.cwd.rstrip("/") + "/" + arg).strip("/").split("/")
        if len(parts) != 2 or parts[0] not in DIRS or not NAME_RE.match(parts[1]) or parts[1] in (".", ".."):
            return None
        return ROOT / parts[0] / parts[1]

    # -- datos (modo pasivo) ----------------------------------------------------------------------- #
    def _data(self) -> Optional[socket.socket]:
        if self.pasv is None:
            self.reply("425 Use PASV first")
            return None
        try:
            self.pasv.settimeout(10)
            conn, _ = self.pasv.accept()
            conn.settimeout(30)
            return conn
        except OSError:
            self.reply("425 Data connection failed")
            return None
        finally:
            self.pasv.close()
            self.pasv = None

    # -- comandos ---------------------------------------------------------------------------------- #
    def handle(self) -> None:
        self.reply("220 obsproto FTP (stand-in)")
        while True:
            try:
                line = self.rfile.readline(512)
            except OSError:
                return
            if not line:
                return
            cmd, _, arg = line.decode("utf-8", "replace").strip().partition(" ")
            cmd = cmd.upper()
            if cmd == "QUIT":
                self.reply("221 Bye")
                return
            if cmd == "USER":
                self.user_ok, self.authed = hmac.compare_digest(arg.encode(), USER.encode()), False
                self.reply("331 Password required")
            elif cmd == "PASS":
                self.authed = self.user_ok and bool(PASSWORD) and hmac.compare_digest(arg.encode(), PASSWORD.encode())
                self.reply("230 Logged in" if self.authed else "530 Login incorrect")
            elif not self.authed:
                self.reply("530 Not logged in")
            elif cmd in ("TYPE", "NOOP", "MODE", "STRU"):
                self.reply("200 OK")
            elif cmd == "SYST":
                self.reply("215 UNIX Type: L8")
            elif cmd == "PWD":
                self.reply(f'257 "{self.cwd}"')
            elif cmd == "CWD":
                d = self._dir(arg)
                if d is None:
                    self.reply("550 No such directory")
                else:
                    self.cwd = "/" + d
                    self.reply("250 OK")
            elif cmd == "PASV":
                if self.pasv:
                    self.pasv.close()
                self.pasv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.pasv.bind(("0.0.0.0", 0))  # noqa: S104 - solo red interna de Docker
                self.pasv.listen(1)
                port = self.pasv.getsockname()[1]
                ip = self.connection.getsockname()[0].replace(".", ",")
                self.reply(f"227 Entering Passive Mode ({ip},{port >> 8},{port & 255})")
            elif cmd == "NLST":
                d = self._dir(arg or self.cwd)
                if d not in DIRS:
                    self.reply("550 No such directory")
                    continue
                conn = self._data()
                if conn:
                    self.reply("150 Listing")
                    names = sorted(p.name for p in (ROOT / d).iterdir() if p.is_file())
                    with conn:
                        conn.sendall("".join(n + "\r\n" for n in names).encode())
                    self.reply("226 Done")
            elif cmd == "STOR":
                path = self._file(arg)
                if path is None:
                    self.reply("553 Invalid file name")
                    continue
                conn = self._data()
                if conn:
                    self.reply("150 Receiving")
                    size, ok = 0, True
                    with conn, open(path, "wb") as fh:
                        while chunk := conn.recv(65536):
                            size += len(chunk)
                            if size > MAX_FILE:
                                ok = False
                                break
                            fh.write(chunk)
                    if ok:
                        self.reply("226 Stored")
                    else:
                        path.unlink(missing_ok=True)
                        self.reply("552 File too large")
            elif cmd == "RETR":
                path = self._file(arg)
                if path is None or not path.is_file():
                    self.reply("550 File not found")
                    continue
                conn = self._data()
                if conn:
                    self.reply("150 Sending")
                    with conn:
                        conn.sendall(path.read_bytes())
                    self.reply("226 Sent")
            elif cmd == "RNFR":
                path = self._file(arg)
                self.rnfr = path if path and path.is_file() else None
                self.reply("350 Ready" if self.rnfr else "550 File not found")
            elif cmd == "RNTO":
                path = self._file(arg)
                if self.rnfr is None or path is None:
                    self.reply("553 Rename failed")
                else:
                    self.rnfr.replace(path)
                    self.reply("250 Renamed")
                self.rnfr = None
            elif cmd == "DELE":
                path = self._file(arg)
                if path and path.is_file():
                    path.unlink()
                    self.reply("250 Deleted")
                else:
                    self.reply("550 File not found")
            else:
                self.reply("502 Not implemented")


class FtpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    if not PASSWORD:
        sys.exit("[config] falta FTP_PASSWORD")
    for d in DIRS:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    server = FtpServer(("0.0.0.0", int(os.environ.get("PORT", "2121"))), FtpHandler)  # noqa: S104 - red interna

    def _stop(*_: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"[ftp] escuchando en :{server.server_address[1]} raíz={ROOT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
