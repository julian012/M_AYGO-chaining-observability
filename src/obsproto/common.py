"""Utilidades compartidas por las capas simuladas (gateway, bus, core, modern).

Solo usa la biblioteca estándar: menos dependencias = menos superficie de ataque
y una imagen más pequeña (ver docs/adr/0003-stdlib-minimal.md).
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

# --------------------------------------------------------------------------- #
# Identificadores: cada capa genera el suyo y registra el de la capa anterior.
# --------------------------------------------------------------------------- #
ID_RE = re.compile(r"^(?:CLI|GW|BUS|CORE|MOD)-[0-9a-f]{12}\Z")
MAX_BODY_BYTES = 4096


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def valid_id(value: Optional[str], prefix: Optional[str] = None) -> bool:
    if not isinstance(value, str) or not ID_RE.match(value):
        return False
    return prefix is None or value.startswith(prefix + "-")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"[config] falta la variable de entorno obligatoria {name}")
    return value or ""


# --------------------------------------------------------------------------- #
# Log "legacy" de ancho fijo (el core no es modificable y solo emite esto).
# El formato está documentado en docs/log-formats.md. Esta tupla es la única
# fuente de verdad para el simulador (escritor) y para el parser (lector).
# --------------------------------------------------------------------------- #
CORE_LAYOUT: Tuple[Tuple[str, int], ...] = (
    ("ts", 17),  # YYYYMMDDHHMMSSmmm (UTC)
    ("core_id", 20),  # ID propio, alineado a la izquierda
    ("parent_id", 20),  # ID recibido del bus
    ("txn", 4),  # TRF | PAY | BAL
    ("amount", 12),  # centavos, ceros a la izquierda
    ("status", 2),  # 00 = OK, 91 = fallo simulado
    ("dur_ms", 6),  # duración, ceros a la izquierda
)
CORE_RECORD_LEN = sum(width for _, width in CORE_LAYOUT)


def format_core_record(
    ts: datetime, core_id: str, parent_id: str, txn: str, amount_cents: int, status: str, dur_ms: int
) -> str:
    stamp = ts.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") + f"{ts.microsecond // 1000:03d}"
    record = (
        stamp.ljust(17)
        + core_id.ljust(20)
        + parent_id.ljust(20)
        + txn.ljust(4)[:4]
        + f"{amount_cents:012d}"[-12:]
        + status.rjust(2, "0")[-2:]
        + f"{min(max(dur_ms, 0), 999999):06d}"
    )
    if len(record) != CORE_RECORD_LEN:  # los IDs deben caber en su campo
        raise ValueError("registro de ancho fijo con longitud inválida")
    return record


# --------------------------------------------------------------------------- #
# Logger JSON de una línea por evento (json.dumps escapa saltos de línea, lo que
# evita la inyección de entradas falsas en el log).
# --------------------------------------------------------------------------- #
class JsonLogger:
    def __init__(self, layer: str, path: str) -> None:
        self.layer = layer
        self._fh = open(path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    def event(self, own_id: str, parent_id: str, **fields: Any) -> None:
        record = {"ts": now_iso(), "layer": self.layer, "id": own_id, "parent_id": parent_id, **fields}
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._fh.write(line + "\n")
        print(line, flush=True)  # visible con `docker compose logs`


# --------------------------------------------------------------------------- #
# Limitador de tasa (token bucket) para el gateway.
# --------------------------------------------------------------------------- #
class RateLimiter:
    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self.rate = rate_per_sec
        self.burst = float(burst)
        self._state: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._state.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - 1.0, now)
            return True


def token_matches(provided: Optional[str], expected: str) -> bool:
    """Comparación en tiempo constante; un token vacío nunca es válido."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def parse_api_keys(raw: str) -> Dict[str, str]:
    """'mobile:abc,web:def' -> {'abc': 'mobile', 'def': 'web'} (clave -> canal)."""
    keys: Dict[str, str] = {}
    for item in raw.split(","):
        channel, _, key = item.strip().partition(":")
        if channel and key:
            keys[key] = channel
    return keys


def channel_for(provided: Optional[str], keys: Dict[str, str]) -> Optional[str]:
    """Busca la clave comparando en tiempo constante contra todas."""
    found = None
    for key, channel in keys.items():
        if token_matches(provided, key):
            found = channel
    return found


# --------------------------------------------------------------------------- #
# HTTP: base de handlers con límites, cabeceras de seguridad y errores genéricos.
# --------------------------------------------------------------------------- #
class HttpError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class BaseHandler(BaseHTTPRequestHandler):
    server_version = "obsproto"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10  # corta conexiones lentas (slowloris)

    def log_message(self, *args: Any) -> None:  # silencia el log por defecto
        return

    # -- respuesta -------------------------------------------------------- #
    def send_json(self, status: int, obj: Any, headers: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> Dict[str, Any]:
        raw_len = self.headers.get("Content-Length", "")
        if not raw_len.isdigit():
            raise HttpError(411, "length_required")
        length = int(raw_len)
        if length == 0 or length > MAX_BODY_BYTES:
            raise HttpError(413, "payload_size")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "invalid_json") from None
        if not isinstance(data, dict):
            raise HttpError(400, "invalid_json")
        return data

    def require_internal(self) -> None:
        """Autenticación servicio-a-servicio con token compartido."""
        if not token_matches(self.headers.get("X-Internal-Token"), env("INTERNAL_TOKEN")):
            raise HttpError(401, "unauthorized")

    def upstream_id(self, prefix: str) -> str:
        value = self.headers.get("X-Upstream-Id")
        if not valid_id(value, prefix):
            raise HttpError(400, "invalid_upstream_id")
        return value  # type: ignore[return-value]

    # -- despacho --------------------------------------------------------- #
    def route(self, method: str, path: str) -> None:  # pragma: no cover - lo define cada capa
        raise HttpError(404, "not_found")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if method == "GET" and path == "/healthz":
                self.send_json(200, {"status": "ok"})
                return
            self.route(method, path)
        except HttpError as exc:
            self.send_json(exc.status, {"error": exc.code})
        except Exception:  # noqa: BLE001 - nunca filtrar detalles internos al cliente
            print(f"[error] fallo no controlado en {method} {path}", file=sys.stderr, flush=True)
            self.send_json(500, {"error": "internal_error"})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self.send_json(405, {"error": "method_not_allowed"})

    do_DELETE = do_PATCH = do_PUT  # noqa: N815


def post_json(
    url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float = 3.0
) -> Tuple[int, Dict[str, Any]]:
    """POST JSON con timeout obligatorio. Devuelve (status, cuerpo)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL interna fija
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 503, {"error": "upstream_unreachable"}


def serve(handler: type, port: int, tls: bool = False) -> None:
    # El backlog por defecto de socketserver es 5: con concurrencia > 5 los SYN se
    # descartan y el cliente reintenta a 1 s, 3 s, 7 s (visto en Docker Desktop).
    ThreadingHTTPServer.request_queue_size = 128
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)  # noqa: S104 - dentro del contenedor
    server.daemon_threads = True
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(env("TLS_CERT", "/certs/server.crt"), env("TLS_KEY", "/certs/server.key"))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    def _stop(*_: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"[{handler.__name__}] escuchando en :{port} tls={tls}", flush=True)
    server.serve_forever()


def timed(fn: Callable[[], Any]) -> Tuple[Any, int]:
    start = time.monotonic()
    result = fn()
    return result, int((time.monotonic() - start) * 1000)
