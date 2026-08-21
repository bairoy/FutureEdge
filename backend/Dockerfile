FROM python:3.11-slim

# curl is used by the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY backend /app

# Run as a non-root user. A container that can place real broker orders should
# not also be able to write anywhere in its own filesystem.
#
# /app must stay writable: the Zerodha OAuth callback persists the (encrypted)
# session token to /app/broker_token.json. Note that docker-compose bind-mounts
# ./backend over /app for development, and the HOST's ownership wins there —
# this chown only governs the built image, i.e. production.
RUN useradd --create-home --uid 1000 futureedge \
    && chown -R futureedge:futureedge /app
USER futureedge

EXPOSE 8000

# Lets an orchestrator see that the process is not merely running but serving.
# start-period covers boot: DB connect, LangGraph checkpointer setup, and the
# kill-switch reconciliation that must finish before any trading machinery.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# PRODUCTION entrypoint — deliberately no --reload.
#
# --reload watches the filesystem and restarts the process on any change. In
# production that turns a stray file write into a mid-trade restart, and it
# costs a whole extra monitoring process. docker-compose.yml overrides this
# command to add --reload for local development only.
#
# --proxy-headers makes uvicorn populate request.client.host from
# X-Forwarded-For, but ONLY for peers in --forwarded-allow-ips (defaults to the
# FORWARDED_ALLOW_IPS env var, falling back to 127.0.0.1). So with nothing
# configured the header is ignored and cannot be spoofed. Behind a proxy, set
# FORWARDED_ALLOW_IPS and TRUSTED_PROXY_IPS to the proxy's address — see
# app/api/dependencies/rate_limiter.py.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--timeout-graceful-shutdown", "3"]
