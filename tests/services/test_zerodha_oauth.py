"""
tests/services/test_zerodha_oauth.py
======================================
Tests for the Zerodha auth path hardening (Implementation Plan, Phase 1).

The Zerodha callback is the one route that mints a live broker session, and
before this phase it was reachable by anyone with the URL. Each test below
pins one property of the fix:

1.  login-url and status require a JWT (no anonymous broker access)
2.  A state is minted with a short TTL and bound to the issuing user
3.  consume_state is single-use — a replayed state is rejected
4.  Every state failure mode (missing / unknown / Redis down) rejects
5.  A callback with no state never reaches generate_session()
6.  A callback with a replayed state never reaches generate_session()
7.  A valid state does reach generate_session()
8.  The token written to Redis is ciphertext, not the raw Kite token
9.  The token written to broker_token.json is ciphertext too
10. The broker decrypts what the callback encrypted (round trip)
11. A legacy plaintext token still loads, so an in-flight session survives
"""

import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.services import oauth_state
from app.services.token_manager import encrypt_token, decrypt_stored_token
from app.api.routes import zerodha_router


RAW_KITE_TOKEN = "aBcDeF1234567890ZyXwVu"


def _fake_request(**query_params) -> MagicMock:
    """Minimal stand-in for a Starlette Request carrying query params."""
    request = MagicMock()
    request.query_params = query_params
    return request


# ============================================================
# TEST 1: THE UNAUTHENTICATED ROUTES ARE NOW AUTHENTICATED
# ============================================================

def test_login_url_and_status_require_auth():
    """
    Both GETs previously had no Depends(...) at all. Assert the dependency is
    actually declared, so deleting it fails a test rather than silently
    reopening anonymous broker access.
    """
    from app.auth.dependencies import require_risk_manager

    for route_fn in (zerodha_router.get_login_url, zerodha_router.get_zerodha_status):
        defaults = [
            d.dependency
            for d in getattr(route_fn, "__defaults__", ()) or ()
            if hasattr(d, "dependency")
        ]
        assert require_risk_manager in defaults, (
            f"{route_fn.__name__} must require an authenticated risk_manager+"
        )


# ============================================================
# TEST 2: STATE IS MINTED, BOUND TO A USER, AND EXPIRES
# ============================================================

@pytest.mark.asyncio
async def test_issue_state_stores_bound_state_with_ttl():
    redis = AsyncMock()

    with patch.object(oauth_state, "redis_client", redis):
        state = await oauth_state.issue_state(user_id="user-42")

    assert state, "a state must be returned"
    assert len(state) >= 32, "state must be long enough to resist guessing"

    redis.set.assert_awaited_once()
    key, value = redis.set.await_args.args
    assert state in key
    assert value == "user-42", "state must be bound to the issuing user"
    assert redis.set.await_args.kwargs["ex"] == oauth_state.STATE_TTL_SECONDS


@pytest.mark.asyncio
async def test_issued_states_are_unique():
    redis = AsyncMock()
    with patch.object(oauth_state, "redis_client", redis):
        states = {await oauth_state.issue_state("u1") for _ in range(50)}
    assert len(states) == 50


# ============================================================
# TEST 3: STATE IS SINGLE-USE
# ============================================================

@pytest.mark.asyncio
async def test_consume_state_burns_the_state_atomically():
    """
    A valid state returns its user once. The burn must be GETDEL (atomic), not
    get-then-delete, or two concurrent callbacks could both pass the check.
    """
    redis = AsyncMock()
    redis.getdel.return_value = "user-42"

    with patch.object(oauth_state, "redis_client", redis):
        user_id = await oauth_state.consume_state("some-state")

    assert user_id == "user-42"
    redis.getdel.assert_awaited_once()


@pytest.mark.asyncio
async def test_replayed_state_is_rejected():
    """Second use of the same state finds nothing in Redis → reject."""
    redis = AsyncMock()
    redis.getdel.side_effect = ["user-42", None]

    with patch.object(oauth_state, "redis_client", redis):
        assert await oauth_state.consume_state("s") == "user-42"
        assert await oauth_state.consume_state("s") is None


# ============================================================
# TEST 4: EVERY STATE FAILURE MODE REJECTS
# ============================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, ""])
async def test_missing_state_rejected_without_touching_redis(bad_state):
    redis = AsyncMock()
    with patch.object(oauth_state, "redis_client", redis):
        assert await oauth_state.consume_state(bad_state) is None
    redis.getdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_state_rejected():
    redis = AsyncMock()
    redis.getdel.return_value = None
    with patch.object(oauth_state, "redis_client", redis):
        assert await oauth_state.consume_state("never-issued") is None


@pytest.mark.asyncio
async def test_redis_outage_rejects_rather_than_admits():
    """Fail closed: if we cannot verify the state, we do not honour it."""
    redis = AsyncMock()
    redis.getdel.side_effect = ConnectionError("redis down")
    with patch.object(oauth_state, "redis_client", redis):
        assert await oauth_state.consume_state("some-state") is None


# ============================================================
# TEST 5-7: THE CALLBACK GATES generate_session() ON STATE
# ============================================================

@pytest.mark.asyncio
async def test_callback_without_state_never_exchanges_the_token():
    """
    The critical assertion of this phase: an unsolicited callback must be
    rejected BEFORE we spend the API secret on generate_session().
    """
    kite_cls = MagicMock()

    with patch.object(zerodha_router, "consume_state", AsyncMock(return_value=None)), \
         patch("kiteconnect.KiteConnect", kite_cls):
        response = await zerodha_router.zerodha_callback(
            _fake_request(request_token="attacker-supplied")
        )

    assert response.status_code == 403
    kite_cls.assert_not_called()
    kite_cls.return_value.generate_session.assert_not_called()


@pytest.mark.asyncio
async def test_callback_with_replayed_state_never_exchanges_the_token():
    kite_cls = MagicMock()
    redis = AsyncMock()
    redis.getdel.return_value = None  # already burned

    with patch.object(oauth_state, "redis_client", redis), \
         patch("kiteconnect.KiteConnect", kite_cls):
        response = await zerodha_router.zerodha_callback(
            _fake_request(request_token="rt", state="already-used")
        )

    assert response.status_code == 403
    kite_cls.assert_not_called()


@pytest.mark.asyncio
async def test_callback_accepts_state_from_redirect_params():
    """
    Kite round-trips custom params via `redirect_params`; depending on the
    app's redirect config the state arrives either flattened onto the query
    string or still packed. Both must be accepted.
    """
    assert zerodha_router._extract_state(_fake_request(state="abc")) == "abc"
    assert zerodha_router._extract_state(
        _fake_request(redirect_params="state=abc")
    ) == "abc"
    assert zerodha_router._extract_state(_fake_request()) is None


# ============================================================
# TEST 8-9: STORED TOKENS ARE CIPHERTEXT
# ============================================================

@pytest.mark.asyncio
async def test_callback_stores_encrypted_token_in_both_sinks(tmp_path, monkeypatch):
    """
    Read access to Redis or broker_token.json must not equal broker access.
    Assert the raw Kite token appears in neither sink, and that what does
    land there decrypts back to the original.
    """
    monkeypatch.chdir(tmp_path)

    kite = MagicMock()
    kite.generate_session.return_value = {"access_token": RAW_KITE_TOKEN}
    kite_cls = MagicMock(return_value=kite)

    redis = AsyncMock()
    broker = AsyncMock()
    publisher = MagicMock()

    with patch.object(zerodha_router, "consume_state", AsyncMock(return_value="user-42")), \
         patch.object(zerodha_router, "redis_client", redis), \
         patch("kiteconnect.KiteConnect", kite_cls), \
         patch("app.brokers.base.get_broker", return_value=broker), \
         patch("app.data.feed.tick_publisher", publisher):
        response = await zerodha_router.zerodha_callback(
            _fake_request(request_token="valid-rt", state="valid-state")
        )

    assert response.status_code == 200
    kite.generate_session.assert_called_once()

    # --- Redis sink ---
    redis.set.assert_awaited_once()
    _, stored_in_redis = redis.set.await_args.args
    assert stored_in_redis != RAW_KITE_TOKEN, "raw token must not reach Redis"
    assert RAW_KITE_TOKEN not in stored_in_redis
    assert decrypt_stored_token(stored_in_redis) == RAW_KITE_TOKEN

    # --- JSON file sink ---
    on_disk = json.loads((tmp_path / "broker_token.json").read_text())
    stored_in_file = on_disk["ZERODHA_ACCESS_TOKEN"]
    assert RAW_KITE_TOKEN not in stored_in_file, "raw token must not reach disk"
    assert decrypt_stored_token(stored_in_file) == RAW_KITE_TOKEN


@pytest.mark.asyncio
async def test_callback_error_page_does_not_leak_exception_text():
    """The failure page sits in browser history — keep exception detail server-side."""
    kite = MagicMock()
    kite.generate_session.side_effect = Exception("checksum mismatch for token sekrit-value")
    kite_cls = MagicMock(return_value=kite)

    with patch.object(zerodha_router, "consume_state", AsyncMock(return_value="user-42")), \
         patch("kiteconnect.KiteConnect", kite_cls):
        response = await zerodha_router.zerodha_callback(
            _fake_request(request_token="rt", state="valid-state")
        )

    assert response.status_code == 500
    assert b"sekrit-value" not in response.body


# ============================================================
# TEST 10-11: THE READ SIDE
# ============================================================

def test_encrypted_token_round_trips():
    assert decrypt_stored_token(encrypt_token(RAW_KITE_TOKEN)) == RAW_KITE_TOKEN


def test_legacy_plaintext_token_still_loads():
    """
    A session stored before encryption was wired in must keep working until it
    expires at the next 6 AM IST rollover, rather than bricking live trading.
    """
    assert decrypt_stored_token(RAW_KITE_TOKEN) == RAW_KITE_TOKEN


def test_missing_token_returns_none_so_callers_fall_through():
    assert decrypt_stored_token(None) is None
    assert decrypt_stored_token("") is None
