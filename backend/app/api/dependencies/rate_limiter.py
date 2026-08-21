"""
app/api/dependencies/rate_limiter.py
======================================
Redis-backed rate limiting dependency for FastAPI.

Provides simple IP-based rate limiting to prevent spamming
and resource exhaustion (e.g. brute-forcing login or spamming workflow runs).

CLIENT IP RESOLUTION (why this is not just `request.client.host`):
-------------------------------------------------------------------
Behind a reverse proxy every request arrives from the proxy's IP, so keying
on the socket peer collapses the whole internet into one bucket — one
attacker's login attempts eat everyone else's quota. The usual fix, trusting
`X-Forwarded-For`, is worse if applied blindly: the header is client-supplied,
so anyone can rotate it per request and never hit a limit at all.

So we trust it only when we know a proxy is in front of us:

  - `TRUSTED_PROXY_IPS` empty (default, direct exposure) → use the socket
    peer, ignore `X-Forwarded-For` completely.
  - `TRUSTED_PROXY_IPS` set and the peer is one of them → take the
    right-most entry of `X-Forwarded-For` that is not itself a trusted proxy.
    Right-most-untrusted is the only position an upstream client cannot
    forge, since the trusted proxy appends the real peer at the end.
  - peer is not a trusted proxy → use the socket peer, header ignored.

DEPLOYMENT (both halves are required):
---------------------------------------
  1. Set `TRUSTED_PROXY_IPS=<proxy-ip>` in `.env`.
  2. Run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy-ip>`
     (already wired into the Dockerfile CMD / compose command).
  3. Make the proxy STRIP any inbound `X-Forwarded-For` before setting its
     own — nginx `proxy_set_header X-Forwarded-For $remote_addr;` (note:
     `$remote_addr`, NOT `$proxy_add_x_forwarded_for`, which appends to
     whatever the client sent). Caddy and Traefik do this by default.
"""

from functools import lru_cache

from fastapi import HTTPException, status, Request
from app.core.config import settings
from app.db.redis import redis_client
from loguru import logger


@lru_cache(maxsize=1)
def _trusted_proxies() -> frozenset[str]:
    """Parse TRUSTED_PROXY_IPS once. Empty set means 'no proxy in front'."""
    raw = (settings.TRUSTED_PROXY_IPS or "").strip()
    if not raw:
        return frozenset()
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


def get_client_ip(request: Request) -> str:
    """
    Resolve the real client IP for rate-limit keying.

    See the module docstring for the trust rules. Returns "unknown" when
    there is no peer at all (e.g. some test transports), which buckets those
    requests together rather than exempting them.
    """
    peer = request.client.host if request.client else None
    trusted = _trusted_proxies()

    # No proxy configured, or the request did not come from one: the socket
    # peer is the only trustworthy source. Ignore any X-Forwarded-For, which
    # at this point can only be client-supplied spoofing.
    if not trusted or peer not in trusted:
        return peer or "unknown"

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer or "unknown"

    # Walk right to left past our own proxy hops; the first non-proxy entry
    # is the closest address our infrastructure actually observed.
    for candidate in reversed([h.strip() for h in forwarded.split(",")]):
        if candidate and candidate not in trusted:
            return candidate

    return peer or "unknown"


def rate_limit(limit: int, window_seconds: int):
    """
    FastAPI dependency factory for rate limiting by client IP.

    Usage:
    ------
        @router.post("/run", dependencies=[Depends(rate_limit(limit=1, window_seconds=30))])
        async def run_workflow(...):
    """
    async def dependency(request: Request):
        client_ip = get_client_ip(request)
        path = request.url.path
        key = f"rate_limit:{path}:{client_ip}"

        try:
            current = await redis_client.incr(key)
            if current == 1:
                # Set TTL on first request of this window
                await redis_client.expire(key, window_seconds)

            if current > limit:
                logger.warning(
                    f"⚠️ Rate limit exceeded | IP={client_ip} | path={path} | "
                    f"count={current}/{limit} | window={window_seconds}s"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Too many requests. Limit: {limit} request(s) "
                        f"every {window_seconds} seconds."
                    ),
                )
        except HTTPException:
            raise
        except Exception as e:
            # Redis failure should NOT block requests in production (fail open),
            # but log the warning.
            logger.error(f"Rate limiting Redis error (failing open): {e}")

    return dependency
