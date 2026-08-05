"""
tests/api/test_rate_limiter.py
================================
Tests for proxy-aware client IP resolution (Implementation Plan, Phase 1).

Rate limiting keyed on the wrong IP fails in one of two directions, and the
fix has to avoid both at once:

  - Behind a proxy, keying on the socket peer buckets every user together,
    so one attacker exhausts everyone's quota.
  - Trusting X-Forwarded-For unconditionally lets any client rotate the
    header per request and never hit a limit at all.

So the header is honoured only when the request actually came from a
configured proxy. These tests pin both halves.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.api.dependencies import rate_limiter


PROXY_IP = "10.0.0.1"
REAL_CLIENT = "203.0.113.9"


def _request(peer: str | None, forwarded: str | None = None) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock(host=peer) if peer else None
    request.headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return request


@pytest.fixture(autouse=True)
def _clear_proxy_cache():
    """TRUSTED_PROXY_IPS is parsed once and cached — reset between tests."""
    rate_limiter._trusted_proxies.cache_clear()
    yield
    rate_limiter._trusted_proxies.cache_clear()


def _set_trusted(monkeypatch, value: str):
    monkeypatch.setattr(rate_limiter.settings, "TRUSTED_PROXY_IPS", value)
    rate_limiter._trusted_proxies.cache_clear()


# ============================================================
# NO PROXY CONFIGURED (the default) → HEADER IS IGNORED
# ============================================================

def test_direct_exposure_uses_socket_peer(monkeypatch):
    _set_trusted(monkeypatch, "")
    assert rate_limiter.get_client_ip(_request(REAL_CLIENT)) == REAL_CLIENT


def test_forwarded_header_is_ignored_when_no_proxy_configured(monkeypatch):
    """
    Without this, any client could bypass every rate limit by sending a
    different X-Forwarded-For on each request.
    """
    _set_trusted(monkeypatch, "")
    spoofed = _request(REAL_CLIENT, forwarded="1.2.3.4")
    assert rate_limiter.get_client_ip(spoofed) == REAL_CLIENT


def test_forwarded_header_is_ignored_from_an_untrusted_peer(monkeypatch):
    """A proxy is configured, but this request did not come through it."""
    _set_trusted(monkeypatch, PROXY_IP)
    spoofed = _request("198.51.100.7", forwarded="1.2.3.4")
    assert rate_limiter.get_client_ip(spoofed) == "198.51.100.7"


# ============================================================
# BEHIND A TRUSTED PROXY → REAL CLIENT IP IS RECOVERED
# ============================================================

def test_trusted_proxy_forwarded_header_is_honoured(monkeypatch):
    _set_trusted(monkeypatch, PROXY_IP)
    req = _request(PROXY_IP, forwarded=REAL_CLIENT)
    assert rate_limiter.get_client_ip(req) == REAL_CLIENT


def test_client_cannot_prepend_a_fake_hop(monkeypatch):
    """
    A client sending its own X-Forwarded-For gets its value pushed left when
    the proxy appends the real peer. Taking the right-most non-proxy entry
    picks the address our infrastructure observed, not the claimed one.
    """
    _set_trusted(monkeypatch, PROXY_IP)
    req = _request(PROXY_IP, forwarded=f"1.2.3.4, {REAL_CLIENT}")
    assert rate_limiter.get_client_ip(req) == REAL_CLIENT


def test_multiple_trusted_proxy_hops_are_skipped(monkeypatch):
    _set_trusted(monkeypatch, f"{PROXY_IP},10.0.0.2")
    req = _request(PROXY_IP, forwarded=f"{REAL_CLIENT}, 10.0.0.2")
    assert rate_limiter.get_client_ip(req) == REAL_CLIENT


def test_trusted_proxy_without_header_falls_back_to_peer(monkeypatch):
    _set_trusted(monkeypatch, PROXY_IP)
    assert rate_limiter.get_client_ip(_request(PROXY_IP)) == PROXY_IP


# ============================================================
# DEGENERATE CASES BUCKET TOGETHER, NEVER EXEMPT
# ============================================================

def test_missing_client_is_bucketed_not_exempted(monkeypatch):
    _set_trusted(monkeypatch, "")
    assert rate_limiter.get_client_ip(_request(None)) == "unknown"


def test_all_hops_trusted_falls_back_to_peer(monkeypatch):
    _set_trusted(monkeypatch, PROXY_IP)
    req = _request(PROXY_IP, forwarded=PROXY_IP)
    assert rate_limiter.get_client_ip(req) == PROXY_IP
