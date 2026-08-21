"""
app/services/stance.py
=========================
The stance matrix — turning a quality verdict plus a valuation band plus
whether you own it into one of BUY / ADD / HOLD / WATCH / EXIT / AVOID.

WHY STANCE IS COMPUTED ON READ AND NEVER STORED:
--------------------------------------------------
Quality changes about once a quarter and costs a full pipeline run. The
valuation band changes when assumptions change. The stance changes with every
tick, and costs one comparison. Storing it would mean re-running the whole
pipeline whenever the price moved, and would leave a stale verb in the database
between runs.

So: quality and band are persisted with their as-of dates; stance is derived
here, live, and carries the rule that produced it.

THE ASYMMETRY IS THE INVESTMENT PHILOSOPHY, NOT AN OVERSIGHT:
---------------------------------------------------------------
                        | not owned      | owned          | quality FAIL
    price < MoS buy     | BUY            | ADD            | AVOID / EXIT
    price inside band   | WATCH @ X      | HOLD           | AVOID / EXIT
    price above band    | WATCH @ X      | HOLD           | AVOID / EXIT

Quality failure overrides price in every row. Price never overrides quality in
any row. In particular there is NO cell where being expensive produces SELL:
the naive rule "price > upper band -> SELL" would churn out of exactly the
compounders worth holding. Both the source method and professional practice
agree that you sell on thesis break, not on price appreciation — expensive is
a reason to stop buying, never a reason to exit a good business.

If you are editing this file to make the matrix symmetric, stop: the asymmetry
is the point.

USAGE:
------
    from app.services.stance import compute_stance

    stance = compute_stance(quality_grade="INVESTMENT_GRADE",
                            current_price=1311.0, band=band, owned=False)
    stance.action        # "WATCH"
    stance.rule_applied  # the exact cell, so the verb is always invertible
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Stance:
    action: str                     # BUY | ADD | HOLD | WATCH | EXIT | AVOID | NOT_RATED
    price_vs_band: str | None       # BELOW_MOS | UNDERVALUED | FAIRLY_VALUED | OVERVALUED
    trigger_price: float | None     # the X in "WATCH @ X"
    rule_applied: str               # which cell fired, in words
    horizon: str = "multi-year, reviewed quarterly"
    computed_at: str = ""

    def __post_init__(self):
        if not self.computed_at:
            self.computed_at = datetime.now(timezone.utc).isoformat()
        # A trigger price is something the reader compares against a live quote.
        # Rendering it as 246.40254936495177 implies a precision the DCF does
        # not have — the whole reason there is a band rather than a number.
        if self.trigger_price is not None:
            self.trigger_price = round(self.trigger_price, 2)


def classify_price(current_price: float, band: dict) -> str | None:
    """Where the price sits relative to the DCF band and the margin-of-safety
    buy price. BELOW_MOS is a stricter bucket than UNDERVALUED."""
    mos = band.get("mos_buy_price")
    lower, upper = band.get("lower_band"), band.get("upper_band")
    if current_price is None or lower is None or upper is None:
        return None
    if mos is not None and current_price < mos:
        return "BELOW_MOS"
    if current_price < lower:
        return "UNDERVALUED"
    if current_price <= upper:
        return "FAIRLY_VALUED"
    return "OVERVALUED"


def compute_stance(
    quality_grade: str,
    current_price: float | None,
    band: dict | None,
    owned: bool,
) -> Stance:
    """
    Apply the matrix. Every branch records `rule_applied`, so the verb can
    always be inverted back into the reasoning that produced it — which is
    what separates a research system from an oracle.
    """
    # NOT_RATED is not a bad verdict, it is a refusal to issue one. It must
    # survive to the surface rather than being resolved into a cautious BUY.
    if quality_grade == "NOT_RATED":
        return Stance(
            action="NOT_RATED", price_vs_band=None, trigger_price=None,
            rule_applied="Quality is NOT_RATED — no stance is issued, at any price.",
        )

    if quality_grade == "NOT_INVESTABLE":
        return Stance(
            action="EXIT" if owned else "AVOID",
            price_vs_band=classify_price(current_price, band or {}),
            trigger_price=None,
            rule_applied=(
                "Quality failed. Quality failure overrides price in every row — "
                + ("exit rather than wait for a better price."
                   if owned else "no price makes this investable.")
            ),
        )

    if not band or current_price is None:
        return Stance(
            action="HOLD" if owned else "NOT_RATED",
            price_vs_band=None, trigger_price=None,
            rule_applied=(
                "Quality passes but there is no valuation band to price against "
                "(the DCF did not complete), so no buy decision is possible."
            ),
        )

    position = classify_price(current_price, band)
    mos = band.get("mos_buy_price")

    if position == "BELOW_MOS":
        return Stance(
            action="ADD" if owned else "BUY",
            price_vs_band=position, trigger_price=mos,
            rule_applied=(
                f"Quality passes and the price is below the margin-of-safety level "
                f"(₹{mos:,.0f}) — the only cell in the matrix that says buy."
            ),
        )

    if owned:
        # Deliberately no SELL cell. Being expensive is a reason to stop
        # buying, never a reason to exit a business that still passes.
        return Stance(
            action="HOLD", price_vs_band=position, trigger_price=mos,
            rule_applied=(
                "Quality still passes, so the thesis is intact. Price is above the "
                "margin-of-safety level, which stops further buying but is never a "
                "reason to sell — exits follow thesis breaks, not price."
            ),
        )

    return Stance(
        action="WATCH", price_vs_band=position, trigger_price=mos,
        rule_applied=(
            f"Quality passes but the price is above the margin-of-safety level. "
            f"Wait for ₹{mos:,.0f} or below."
            if mos else "Quality passes; price is above the buy level."
        ),
    )
