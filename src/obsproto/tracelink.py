"""Cómo queda el tracenumber del core en el log del bus (política ``BUS_TRACE_LOG``).

Caso real: el core (tipo Stratus) no recibe el ID del bus; asigna un consecutivo por movimiento (el
tracenumber) y lo devuelve en la respuesta. El único lugar donde puede quedar el eslabón bus -> core es
el log del bus, y ahí el valor suele quedar ofuscado. La política define si el eslabón sobrevive:

  clear   trace=000123456789      unión determinista; el valor queda expuesto en el log
  token   trace=tok_9f2c41ab…     HMAC-SHA256 con llave: unión determinista SIN exponer el valor
                                  (el correlacionador calcula el mismo token sobre el log del core)
  mask    trace=********6789      solo quedan los últimos N dígitos: unión por inferencia (dígitos + hora),
                                  ambigua si en la ventana hay dos movimientos con la misma terminación
  none    (no se registra)        el eslabón se pierde
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Optional

POLICIES = ("clear", "token", "mask", "none")
TRACE_DIGITS = 12
TOKEN_RE = re.compile(r"^tok_[0-9a-f]{16}\Z")
CLEAR_RE = re.compile(r"^[0-9]{12}\Z")
MASK_RE = re.compile(r"^\*{1,11}[0-9]{1,11}\Z")


def token(tracenumber: str, key: str) -> str:
    digest = hmac.new(key.encode(), tracenumber.encode(), hashlib.sha256).hexdigest()
    return "tok_" + digest[:16]


def mask(tracenumber: str, visible: int) -> str:
    visible = max(1, min(visible, TRACE_DIGITS - 1))
    return "*" * (TRACE_DIGITS - visible) + tracenumber[-visible:]


def obfuscate(tracenumber: str, policy: str, key: str = "", visible: int = 4) -> Optional[str]:
    """Valor que se escribe en el log del bus, o None si la política es no registrarlo."""
    if policy == "clear":
        return tracenumber
    if policy == "token":
        return token(tracenumber, key)
    if policy == "mask":
        return mask(tracenumber, visible)
    return None


def kind(value: str) -> Optional[str]:
    """Clasifica un valor leído del log: 'clear', 'token', 'mask' o None si no es válido."""
    if CLEAR_RE.match(value):
        return "clear"
    if TOKEN_RE.match(value):
        return "token"
    if MASK_RE.match(value) and len(value) == TRACE_DIGITS:
        return "mask"
    return None
