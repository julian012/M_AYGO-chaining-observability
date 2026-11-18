import pytest

from obsproto.common import (
    ID_RE,
    HttpError,
    RateLimiter,
    channel_for,
    new_id,
    parse_api_keys,
    token_matches,
    valid_id,
)
from obsproto.schema import validate_txn


def test_ids_have_expected_shape():
    for prefix in ("CLI", "GW", "BUS", "CORE", "MOD"):
        assert ID_RE.match(new_id(prefix))
    assert len({new_id("GW") for _ in range(1000)}) == 1000


@pytest.mark.parametrize(
    "bad", [None, "", "GW-xyz", "gw-aaaaaaaaaaaa", "GW-AAAAAAAAAAAA", "GW-aaaaaaaaaaaa\n", "XX-aaaaaaaaaaaa", 5]
)
def test_valid_id_rejects(bad):
    assert not valid_id(bad)


def test_valid_id_prefix():
    assert valid_id("GW-aaaaaaaaaaaa", "GW")
    assert not valid_id("GW-aaaaaaaaaaaa", "BUS")


def test_token_matches():
    assert token_matches("abc", "abc")
    assert not token_matches("abd", "abc")
    assert not token_matches("", "")  # nunca válido
    assert not token_matches(None, "abc")


def test_api_keys_map_to_channel():
    keys = parse_api_keys("mobile:k1, web:k2 ,partner:k3,malformed")
    assert keys == {"k1": "mobile", "k2": "web", "k3": "partner"}
    assert channel_for("k2", keys) == "web"
    assert channel_for("nope", keys) is None
    assert channel_for(None, keys) is None


def test_rate_limiter_burst_and_refill():
    rl = RateLimiter(rate_per_sec=1, burst=2)
    assert rl.allow("a", now=0.0) and rl.allow("a", now=0.0)
    assert not rl.allow("a", now=0.0)
    assert rl.allow("b", now=0.0)  # canales independientes
    assert rl.allow("a", now=1.1)  # se repone


GOOD = {"type": "TRF", "account": "123456789", "amount_cents": 1500}


def test_account_rejects_trailing_newline():
    with pytest.raises(HttpError):
        validate_txn({"type": "TRF", "account": "123456\n", "amount_cents": 1})


def test_validate_ok():
    assert validate_txn(dict(GOOD)) == GOOD


@pytest.mark.parametrize(
    "mutation",
    [
        {"type": "DROP"},
        {"account": "12ab"},
        {"account": "1" * 21},
        {"account": 123456},
        {"amount_cents": -1},
        {"amount_cents": 10_000_001},
        {"amount_cents": True},
        {"amount_cents": 1.5},
        {"amount_cents": "10"},
    ],
)
def test_validate_rejects(mutation):
    with pytest.raises(HttpError) as exc:
        validate_txn({**GOOD, **mutation})
    assert exc.value.status == 422


def test_validate_rejects_extra_or_missing_fields():
    with pytest.raises(HttpError):
        validate_txn({**GOOD, "extra": 1})
    with pytest.raises(HttpError):
        validate_txn({"type": "TRF"})
