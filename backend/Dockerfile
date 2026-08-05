FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY backend /app

# --proxy-headers makes uvicorn populate request.client.host from
# X-Forwarded-For, but ONLY for peers listed in --forwarded-allow-ips.
# That list defaults to the FORWARDED_ALLOW_IPS env var, and uvicorn's own
# fallback is 127.0.0.1 — so with nothing configured the header is ignored
# and cannot be spoofed. When deploying behind a proxy, set both
# FORWARDED_ALLOW_IPS and TRUSTED_PROXY_IPS to the proxy's address
# (see app/api/dependencies/rate_limiter.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--reload", "--timeout-graceful-shutdown", "3"]