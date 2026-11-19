"""Transporte realista: tramas por TCP (bus -> core) y archivos planos por FTP (core -> moderno)."""

import ftplib
import io
import socket
import threading

import pytest

from obsproto import ftpserver
from obsproto.wire import (
    NOVEDAD_LEN,
    novedad_detail,
    novedad_header,
    novedad_trailer,
    parse_novedades,
    tcp_exchange,
    tcp_frame,
    tcp_read_frame,
)


# ------------------------------------------------------------------ TCP
def test_tcp_frame_roundtrip_with_length_header():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(tcp_frame(b"0200HOLA") + tcp_frame(b"0200CHAO"))  # dos mensajes seguidos en la misma conexión
        assert tcp_read_frame(b) == b"0200HOLA"
        assert tcp_read_frame(b) == b"0200CHAO"
        a.close()
        assert tcp_read_frame(b) is None  # cierre limpio


@pytest.mark.parametrize("raw", [b"00AB", b"9999", b"0000"])
def test_tcp_rejects_bad_length_header(raw):
    a, b = socket.socketpair()
    with a, b:
        a.sendall(raw + b"x" * 10)
        with pytest.raises(ValueError):
            tcp_read_frame(b)


def test_tcp_exchange_with_a_server_and_when_it_is_down():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve_once():
        conn, _ = srv.accept()
        with conn:
            frame = tcp_read_frame(conn)
            conn.sendall(tcp_frame(b"0210" + frame[4:]))

    t = threading.Thread(target=serve_once)
    t.start()
    assert tcp_exchange("127.0.0.1", port, b"0200ABC") == b"0210ABC"
    t.join()
    srv.close()
    assert tcp_exchange("127.0.0.1", port, b"0200ABC", timeout=0.5) is None  # core caído


# ------------------------------------------------------------------ archivo plano
def novedades(n=2):
    det = [novedad_detail(f"CORE-{795071260648 + i:012d}", "PAY", 207101, "00", "20261119065452908") for i in range(n)]
    return "\n".join([novedad_header("20261119065455000", "CORE01"), *det, novedad_trailer(n)]) + "\n"


def test_novedades_roundtrip():
    text = novedades(3)
    assert all(len(ln) == NOVEDAD_LEN for ln in text.splitlines())
    d = parse_novedades(text)
    assert [x["core_id"] for x in d] == ["CORE-795071260648", "CORE-795071260649", "CORE-795071260650"]
    assert d[0]["amount"] == 207101 and d[0]["status"] == "00"


def test_novedades_integrity():
    text = novedades(3)
    lines = text.splitlines()
    assert parse_novedades("\n".join(lines[:-1])) is None  # sin cola: transferencia cortada
    assert parse_novedades("\n".join([lines[0], lines[1], lines[3]])) is None  # falta un registro: conteo no cuadra
    assert parse_novedades(text.replace("CORE-795071260649", "BUS-7950712606490")) is None  # ID que no es del core
    assert parse_novedades("") is None


# ------------------------------------------------------------------ FTP (stand-in + ftplib real)
@pytest.fixture
def ftp_server(tmp_path, monkeypatch):
    monkeypatch.setattr(ftpserver, "ROOT", tmp_path)
    monkeypatch.setattr(ftpserver, "USER", "novedades")
    monkeypatch.setattr(ftpserver, "PASSWORD", "secreto")
    for d in ftpserver.DIRS:
        (tmp_path / d).mkdir()
    server = ftpserver.FtpServer(("127.0.0.1", 0), ftpserver.FtpHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], tmp_path
    server.shutdown()
    server.server_close()


def test_ftp_upload_list_download_and_move(ftp_server):
    port, root = ftp_server
    content = novedades(2).encode()
    with ftplib.FTP() as ftp:  # noqa: S321 - se prueba el FTP de la red interna
        ftp.connect("127.0.0.1", port, timeout=5)
        ftp.login("novedades", "secreto")
        ftp.storbinary("STOR inbox/NOV_1.tmp", io.BytesIO(content))
        ftp.rename("inbox/NOV_1.tmp", "inbox/NOV_1.txt")
        assert [p.rsplit("/", 1)[-1] for p in ftp.nlst("inbox")] == ["NOV_1.txt"]
        buf = io.BytesIO()
        ftp.retrbinary("RETR inbox/NOV_1.txt", buf.write)
        assert buf.getvalue() == content
        ftp.rename("inbox/NOV_1.txt", "procesados/NOV_1.txt")
    assert (root / "procesados" / "NOV_1.txt").read_bytes() == content
    assert not any((root / "inbox").iterdir())


def test_ftp_rejects_bad_login(ftp_server):
    port, _ = ftp_server
    with ftplib.FTP() as ftp:  # noqa: S321 - se prueba el FTP de la red interna
        ftp.connect("127.0.0.1", port, timeout=5)
        with pytest.raises(ftplib.error_perm):
            ftp.login("novedades", "otra")


@pytest.mark.parametrize("name", ["../../etc/passwd", "inbox/../x", "/otra/x.txt", "inbox/a b.txt", "inbox/.."])
def test_ftp_rejects_paths_outside_its_folders(ftp_server, name):
    port, _ = ftp_server
    with ftplib.FTP() as ftp:  # noqa: S321 - se prueba el FTP de la red interna
        ftp.connect("127.0.0.1", port, timeout=5)
        ftp.login("novedades", "secreto")
        with pytest.raises(ftplib.error_perm):
            ftp.storbinary(f"STOR {name}", io.BytesIO(b"x"))
