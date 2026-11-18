"""Healthcheck para contenedores sin curl: `python -m obsproto.healthcheck`."""

from __future__ import annotations

import os
import ssl
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("PORT", "8080")
    tls = os.environ.get("TLS", "0") == "1"
    ctx = ssl._create_unverified_context() if tls else None  # solo localhost  # noqa: S323,SLF001
    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=2, context=ctx) as resp:  # noqa: S310
            return 0 if resp.status == 200 else 1
    except Exception:  # noqa: BLE001
        return 1


if __name__ == "__main__":
    sys.exit(main())
