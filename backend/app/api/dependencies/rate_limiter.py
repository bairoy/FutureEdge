"""
app/api/dependencies/rate_limiter.py
======================================
Redis-backed rate limiting dependency for FastAPI.

Provides simple IP-based rate limiting to prevent spamming
and resource exhaustion (e.g. brute-forcing login or spamming workflow runs).
"""

from fastapi import HTTPException, status, Request
from app.db.redis import redis_client
from loguru import logger


def rate_limit(limit: int, window_seconds: int):
    """
    FastAPI dependency factory for rate limiting by client IP.

    Usage:
    ------
        @router.post("/run", dependencies=[Depends(rate_limit(limit=1, window_seconds=30))])
        async def run_workflow(...):
    """
    async def dependency(request: Request):
        client_ip = request.client.host if request.client else "unknown"
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
