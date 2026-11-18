"""Esquema de transacción compartido por gateway, bus y core (lista blanca estricta)."""

from __future__ import annotations

import re
from typing import Dict

from obsproto.common import HttpError

TXN_TYPES = ("TRF", "PAY", "BAL")
ACCOUNT_RE = re.compile(r"^[0-9]{6,20}\Z")
MAX_AMOUNT_CENTS = 10_000_000


def validate_txn(body: Dict[str, object]) -> Dict[str, object]:
    """Lista blanca estricta: campos conocidos, tipos exactos, rangos acotados."""
    if set(body) != {"type", "account", "amount_cents"}:
        raise HttpError(422, "invalid_fields")
    txn, account, amount = body["type"], body["account"], body["amount_cents"]
    if txn not in TXN_TYPES:
        raise HttpError(422, "invalid_type")
    if not isinstance(account, str) or not ACCOUNT_RE.match(account):
        raise HttpError(422, "invalid_account")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= MAX_AMOUNT_CENTS:
        raise HttpError(422, "invalid_amount")
    return {"type": txn, "account": account, "amount_cents": amount}
