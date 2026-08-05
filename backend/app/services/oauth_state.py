"""
app/services/oauth_state.py
============================
Single-use CSRF `state` tokens for the Zerodha OAuth round trip.

WHY THIS EXISTS:
-----------------
`GET /auth/zerodha/callback` is the one broker route that cannot be
protected with `Depends(require_admin)`. Zerodha redirects the operator's
*browser* there, and a browser redirect carries no `Authorization: Bearer`
header — this app has no cookie-based session to fall back on. Left open,
anyone who can reach the callback URL can feed us a `request_token` and
make the backend mint a Kite session against our API secret.

The `state` token is what authenticates that callback instead:

  1. `login-url` is admin-only. It mints a random state, binds it to the
     calling user's id, and stores it in Redis with a short TTL.
  2. The state rides through Zerodha and comes back on the callback.
  3. `callback` consumes the state. No state, unknown state, or a state
     already used once → reject *before* touching `generate_session()`.

Consumption is atomic (`GETDEL`), so a replayed callback finds nothing and
is rejected — the state is a one-shot capability, not a password.

USAGE:
------
    from app.services.oauth_state import issue_state, consume_state

    state = await issue_state(user_id=current_user.id)      # in login-url
    user_id = await consume_state(state)                     # in callback
    if user_id is None:
        raise HTTPException(403, "Invalid or expired OAuth state")
"""

import secrets

from loguru import logger

from app.db.redis import redis_client, KEY_ZERODHA_OAUTH_STATE

# How long an operator has to finish the Zerodha login after clicking
# "Connect". Long enough for a 2FA/TOTP prompt, short enough that a
# leaked state is not a standing capability.
STATE_TTL_SECONDS = 600


async def issue_state(user_id: str) -> str:
    """
    Mint a new single-use OAuth state bound to `user_id` and store it.

    Returns the state string to embed in the Zerodha login URL.
    Raises if Redis is unreachable — we fail closed here rather than
    hand out a state we cannot later verify.
    """
    state = secrets.token_urlsafe(32)
    key = KEY_ZERODHA_OAUTH_STATE.format(state=state)

    await redis_client.set(key, str(user_id), ex=STATE_TTL_SECONDS)
    logger.info(f"Issued Zerodha OAuth state for user={user_id} (ttl={STATE_TTL_SECONDS}s)")

    return state


async def consume_state(state: str | None) -> str | None:
    """
    Atomically validate and burn a state token.

    Returns the issuing user's id if the state was valid, else None.
    A None/empty state, an unknown state, a Redis outage, or a state that
    was already consumed all return None — every failure mode is a reject.
    """
    if not state:
        return None

    key = KEY_ZERODHA_OAUTH_STATE.format(state=state)

    try:
        # GETDEL makes validate-and-burn a single atomic step, so two
        # concurrent callbacks with the same state cannot both pass.
        user_id = await redis_client.getdel(key)
    except AttributeError:
        # redis-py < 4.4 has no getdel(). Fall back to a pipeline, which
        # is still atomic on the server.
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            user_id, _ = await pipe.execute()
    except Exception as e:
        logger.error(f"OAuth state lookup failed (rejecting callback): {e}")
        return None

    if not user_id:
        logger.warning("Zerodha callback presented an unknown or expired OAuth state")
        return None

    return user_id
