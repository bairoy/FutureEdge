"""
tests/services/test_stance.py
===============================
Tests for the stance matrix.

This file exists mostly to defend ONE property: there is no cell where a
quality company being expensive produces SELL.

The naive rule — "price above the band, therefore sell" — is the obvious thing
to write and it is wrong for long-term investing. It churns out of exactly the
compounders worth holding. Both the source method and professional practice
agree that you sell on thesis break, not on price appreciation: expensive is a
reason to stop buying, never a reason to exit a business that still passes.

Anyone "fixing" the matrix to be symmetric will fail these tests. That is the
intent.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.services.stance import compute_stance, classify_price

# RELIANCE's real numbers from a live DCF run.
BAND = {"lower_band": 713.0, "upper_band": 871.0, "mos_buy_price": 499.0, "complete": True}


# ============================================================
# The asymmetry — the reason this file exists
# ============================================================

@pytest.mark.parametrize("price", [900.0, 1311.0, 5000.0, 100_000.0])
def test_expensive_never_produces_sell_for_a_quality_company(price):
    """
    At any price, however absurd, a company that still passes on quality is
    HOLD if owned — never SELL. Exits follow thesis breaks, not price.
    """
    stance = compute_stance("INVESTMENT_GRADE", price, BAND, owned=True)
    assert stance.action == "HOLD"
    assert stance.action not in ("SELL", "EXIT", "REDUCE")


def test_quality_failure_overrides_price_in_every_row():
    """Even far below the margin of safety, a failed business is not a buy."""
    for price in (10.0, 499.0, 800.0, 5000.0):
        assert compute_stance("NOT_INVESTABLE", price, BAND, owned=False).action == "AVOID"
        assert compute_stance("NOT_INVESTABLE", price, BAND, owned=True).action == "EXIT"


# ============================================================
# The matrix, cell by cell
# ============================================================

@pytest.mark.parametrize("price,owned,expected", [
    (450.0,  False, "BUY"),      # below margin of safety, not held
    (450.0,  True,  "ADD"),      # below margin of safety, held
    (800.0,  False, "WATCH"),    # inside band, not held
    (800.0,  True,  "HOLD"),     # inside band, held
    (1311.0, False, "WATCH"),    # above band, not held
    (1311.0, True,  "HOLD"),     # above band, held
])
def test_matrix_cells(price, owned, expected):
    assert compute_stance("INVESTMENT_GRADE", price, BAND, owned).action == expected


def test_ownership_is_what_separates_buy_from_add_and_watch_from_hold():
    """
    The same numbers give different answers depending only on whether the
    position exists — which is why the holdings register is not optional.
    """
    for price in (450.0, 800.0):
        assert (compute_stance("INVESTMENT_GRADE", price, BAND, owned=False).action
                != compute_stance("INVESTMENT_GRADE", price, BAND, owned=True).action)


# ============================================================
# NOT_RATED must survive to the surface
# ============================================================

def test_not_rated_is_never_resolved_into_a_stance():
    """
    NOT_RATED is a refusal to issue a verdict, not a cautious one. Turning it
    into WATCH or HOLD would launder "we do not know" into "we looked".
    """
    for price in (100.0, 800.0, 5000.0):
        for owned in (True, False):
            stance = compute_stance("NOT_RATED", price, BAND, owned)
            assert stance.action == "NOT_RATED"
            assert "no stance" in stance.rule_applied.lower()


def test_no_valuation_band_means_no_buy_decision():
    """Quality alone cannot justify a purchase — a great business at a bad
    price is still a bad purchase, and without a band there is no price test."""
    assert compute_stance("INVESTMENT_GRADE", 800.0, None, owned=False).action == "NOT_RATED"
    assert compute_stance("INVESTMENT_GRADE", 800.0, None, owned=True).action == "HOLD"


def test_missing_price_means_no_buy_decision():
    assert compute_stance("INVESTMENT_GRADE", None, BAND, owned=False).action == "NOT_RATED"


# ============================================================
# Every verb must be invertible
# ============================================================

@pytest.mark.parametrize("grade,price,owned", [
    ("INVESTMENT_GRADE", 450.0, False),
    ("INVESTMENT_GRADE", 1311.0, True),
    ("NOT_INVESTABLE", 450.0, True),
    ("NOT_RATED", 800.0, False),
])
def test_every_stance_states_the_rule_that_produced_it(grade, price, owned):
    """
    rule_applied is what makes this a research system rather than an oracle:
    the verb can always be traced back to the cell that produced it.
    """
    stance = compute_stance(grade, price, BAND, owned)
    assert stance.rule_applied and len(stance.rule_applied) > 20
    assert stance.horizon


def test_trigger_price_is_the_margin_of_safety_level():
    """"WATCH @ X" has to name the X, or it is not actionable."""
    stance = compute_stance("INVESTMENT_GRADE", 1311.0, BAND, owned=False)
    assert stance.trigger_price == BAND["mos_buy_price"]
    assert "499" in stance.rule_applied


# ============================================================
# Price classification
# ============================================================

@pytest.mark.parametrize("price,expected", [
    (450.0,  "BELOW_MOS"),       # stricter than merely undervalued
    (600.0,  "UNDERVALUED"),
    (800.0,  "FAIRLY_VALUED"),
    (713.0,  "FAIRLY_VALUED"),   # lower band is inclusive
    (871.0,  "FAIRLY_VALUED"),   # upper band is inclusive
    (900.0,  "OVERVALUED"),
])
def test_classify_price(price, expected):
    assert classify_price(price, BAND) == expected


def test_classify_price_without_a_band_returns_none():
    assert classify_price(800.0, {}) is None
