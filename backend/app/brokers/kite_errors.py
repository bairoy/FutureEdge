"""
app/brokers/kite_errors.py
============================
Classify Kite Connect failures into an action, instead of catching everything
with a bare `except Exception` and retrying forever.

WHY THIS EXISTS:
-----------------
Every broker call site used to swallow failures identically: log the message,
carry on, try again next cycle. Kite's failure modes are not interchangeable,
and treating them as one thing has already cost us live incidents.

On 2026-08-05 a live SHORT was rejected with *"No IPs configured for this app"*
(Kite gates order placement behind a static-IP allowlist). That condition can
only be cleared by a human editing the Kite developer console — but the bare
handler retried it every few seconds for an hour, logging identical errors and
alerting nobody. The same shape of bug hides an expired access token: every
order silently fails while the system happily keeps proposing trades and the
operator has no idea their positions are unmanaged.

THE POLICY:
------------
  RETRY  — transient, will likely resolve itself. Say nothing, try again.
           NetworkException, DataException (Kite ↔ OMS hiccups).

  FAIL   — this call cannot succeed, but the system can keep running.
           Alert once; do not retry this operation.
           InputException (bad symbol/params), OrderException (margin,
           circuit limit), GeneralException.

  HALT   — nothing can trade until a human intervenes. Trip the kill switch
           and alert. TokenException (dead session), PermissionException
           (missing IP allowlist, revoked app permissions).

WHY HALT ON THESE:
-------------------
A dead session or a missing IP allowlist breaks order placement *entirely* —
including the exit orders that protect open positions. Continuing to run the
agent pipeline in that state manufactures proposals that cannot execute while
real positions sit unguarded. Halting is the honest response, and the kill
switch fails closed (see kill_switch_service), so it persists until a human
explicitly releases it — which is exactly right, because a human is the only
thing that can fix either cause.

USAGE:
-------
    from app.brokers.kite_errors import handle_kite_error, KiteAction

    try:
        ...broker call...
    except Exception as e:
        action = await handle_kite_error(e, context="exit_monitor:SBIN")
        if action is KiteAction.RETRY:
            continue          # transient — next cycle will pick it up
        return                # FAIL/HALT are both non-retryable here
"""

from enum import Enum

from loguru import logger


class KiteAction(str, Enum):
    """What the caller should do about a broker failure."""

    RETRY = "RETRY"   # transient — safe to try again
    FAIL = "FAIL"     # this call is dead; system keeps running
    HALT = "HALT"     # trading stopped; needs a human


# Kite exception class name → action. Keyed by NAME rather than by the class
# itself so this module never hard-depends on kiteconnect being installed
# (unit tests and the paper-only deployment path do not need it).
_ACTION_BY_EXC_NAME: dict[str, KiteAction] = {
    # Transient plumbing between Kite and the exchange's OMS.
    "NetworkException":    KiteAction.RETRY,
    "DataException":       KiteAction.RETRY,

    # The session or the app itself is unusable — a human must fix it.
    "TokenException":      KiteAction.HALT,
    "PermissionException": KiteAction.HALT,

    # This particular call is malformed or refused; others may still work.
    "InputException":      KiteAction.FAIL,
    "OrderException":      KiteAction.FAIL,
    "GeneralException":    KiteAction.FAIL,
}

# Substrings that identify an unrecoverable app-configuration problem even when
# Kite reports it under a vaguer exception class. Kite has moved the static-IP
# error between error_types before, and misclassifying it as retryable is what
# produced the 2026-08-05 retry storm.
_HALT_MESSAGE_MARKERS = (
    "no ips configured",
    "add allowed ips",
)


def classify_kite_error(exc: BaseException) -> KiteAction:
    """
    Map an exception to the action its caller should take.

    Unknown/non-Kite exceptions classify as FAIL: loud and non-retrying is the
    safer default on a money-moving path than an infinite quiet retry.
    """
    message = str(exc).lower()
    if any(marker in message for marker in _HALT_MESSAGE_MARKERS):
        return KiteAction.HALT

    # Walk the MRO so subclasses of a known Kite exception inherit its action.
    for klass in type(exc).__mro__:
        action = _ACTION_BY_EXC_NAME.get(klass.__name__)
        if action is not None:
            return action

    return KiteAction.FAIL


async def handle_kite_error(exc: BaseException, *, context: str) -> KiteAction:
    """
    Classify a broker failure, log it at the right level, and run its side
    effects (kill switch + Telegram alert for HALT, alert only for FAIL).

    `context` should say where this happened and on what, e.g.
    "execution_agent:RELIANCE" — it is included in the operator alert.

    Returns the action so the caller can decide whether to retry. Never raises:
    a failure inside error handling must not mask the original error.
    """
    action = classify_kite_error(exc)
    exc_name = type(exc).__name__

    if action is KiteAction.RETRY:
        logger.warning(f"Kite transient error [{context}] {exc_name}: {exc} — will retry")
        return action

    if action is KiteAction.FAIL:
        logger.error(f"Kite non-retryable error [{context}] {exc_name}: {exc}")
        await _alert(f"⚠️ Broker call failed\n\nWhere: {context}\n{exc_name}: {exc}")
        return action

    # ---- HALT ----
    logger.critical(
        f"🛑 Kite unrecoverable error [{context}] {exc_name}: {exc} — halting trading"
    )

    reason = f"Broker unusable ({exc_name}) at {context}: {exc}"
    try:
        from app.services.kill_switch_service import activate_kill_switch

        # duration_seconds=None → no expiry. This must persist until a human
        # fixes the token/allowlist and explicitly releases it.
        await activate_kill_switch(duration_seconds=None, reason=reason, user_id=None)
    except Exception as halt_err:
        logger.critical(
            f"FAILED TO ACTIVATE KILL SWITCH after broker error: {halt_err} — "
            f"trading may continue against an unusable broker"
        )

    await _alert(
        f"🛑 TRADING HALTED\n\n"
        f"{exc_name} at {context}\n{exc}\n\n"
        f"The broker cannot place orders, so open positions are unmanaged. "
        f"Fix the cause, then release the kill switch from the dashboard."
    )
    return action


async def _alert(text: str) -> None:
    """
    Best-effort operator notification.

    Alerting is never allowed to raise into the trading path — a Telegram
    outage must not turn a handled broker error into an unhandled one.
    """
    try:
        from app.services.telegram_service import send_telegram_message

        await send_telegram_message(text)
    except Exception as alert_err:
        logger.error(f"Could not send broker alert: {alert_err}")
