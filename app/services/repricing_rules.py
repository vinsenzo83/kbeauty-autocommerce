from __future__ import annotations

"""
app/services/repricing_rules.py
─────────────────────────────────
Sprint 13 – Market-aware recommended price algorithm.

Public API
----------
compute_recommended_price(
    supplier_cost, shipping_cost, fee_rate, target_margin_rate, min_margin_abs,
    competitor_band=None,
    lower_bound_factor=0.97,   # lower_bound = competitor_min * factor
    upper_bound_factor=1.05,   # upper_bound = competitor_median * factor
) -> RecommendedPrice

All functions are pure / stateless so they can be unit-tested in isolation.

Algorithm
---------
1.  base_price = (supplier_cost + shipping_cost) / (1 - target_margin_rate - fee_rate)
2.  Enforce min_margin_abs (same as pricing_rules.py)
3.  Round to *.99
4.  If competitor band available:
        lower_bound = competitor_min  * lower_bound_factor    (default 0.97)
        upper_bound = competitor_median * upper_bound_factor  (default 1.05)
        recommended = clamp(base_rounded, lower_bound, upper_bound)
5.  expected_margin_pct at recommended price

Configurable via constructor / per-call keyword args.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from app.services.market_price_service import CompetitorBand


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RecommendedPrice:
    base_price:           Decimal   # cost+margin pre-rounding
    base_rounded:         Decimal   # *.99 rounded base
    recommended_price:    Decimal   # after competitor clamping
    lower_bound:          Decimal | None
    upper_bound:          Decimal | None
    expected_margin_pct:  float     # gross margin % at recommended price
    reason:               str       # '' / 'min_margin_enforced' / 'clamped_up' / 'clamped_down'
    competitor_band:      CompetitorBand | None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (re-used from pricing_rules logic)
# ─────────────────────────────────────────────────────────────────────────────

_ZERO = Decimal("0")
_ONE  = Decimal("1")


def _apply_rounding_usd(price: Decimal) -> Decimal:
    """Floor to nearest dollar, then add 0.99."""
    floor_val = int(price.to_integral_value(rounding=ROUND_FLOOR))
    return Decimal(f"{floor_val}.99")


def _round2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"))


def _expected_margin_pct(
    sell_price: Decimal,
    supplier_cost: Decimal,
    shipping_cost: Decimal,
    fee_rate: Decimal,
) -> float:
    """
    gross_margin = sell_price - supplier_cost - shipping_cost - (sell_price * fee_rate)
    margin_pct   = gross_margin / sell_price
    """
    if sell_price <= _ZERO:
        return 0.0
    fee          = sell_price * fee_rate
    gross_margin = sell_price - supplier_cost - shipping_cost - fee
    return float((gross_margin / sell_price * 100).quantize(Decimal("0.01")))


# ─────────────────────────────────────────────────────────────────────────────
# Main computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_recommended_price(
    supplier_cost: float | Decimal,
    shipping_cost: float | Decimal = 3.00,
    fee_rate: float | Decimal = 0.03,
    target_margin_rate: float | Decimal = 0.30,
    min_margin_abs: float | Decimal = 3.00,
    competitor_band: CompetitorBand | None = None,
    lower_bound_factor: float | Decimal = Decimal("0.97"),
    upper_bound_factor: float | Decimal = Decimal("1.05"),
) -> RecommendedPrice:
    """
    Compute market-aware recommended sell price.

    Parameters
    ----------
    supplier_cost       : Best supplier unit cost.
    shipping_cost       : Shipping buffer (default $3.00).
    fee_rate            : Platform + payment fee (default 3 %).
    target_margin_rate  : Target gross margin (default 30 %).
    min_margin_abs      : Minimum absolute margin floor (default $3.00).
    competitor_band     : CompetitorBand from market_price_service, or None.
    lower_bound_factor  : competitor_min * factor = lower bound (default 0.97).
    upper_bound_factor  : competitor_median * factor = upper bound (default 1.05).

    Returns
    -------
    RecommendedPrice dataclass.
    """
    sc  = Decimal(str(supplier_cost))
    sh  = Decimal(str(shipping_cost))
    fr  = Decimal(str(fee_rate))
    tmr = Decimal(str(target_margin_rate))
    mma = Decimal(str(min_margin_abs))
    lbf = Decimal(str(lower_bound_factor))
    ubf = Decimal(str(upper_bound_factor))

    # ── Step 1: Base cost+margin price ────────────────────────────────────────
    denominator = _ONE - tmr - fr
    if denominator <= _ZERO:
        raise ValueError(
            f"target_margin_rate ({target_margin_rate}) + fee_rate ({fee_rate}) must be < 1.0"
        )

    cost      = sc + sh
    base_raw  = cost / denominator

    # ── Step 2: Enforce minimum absolute margin ───────────────────────────────
    min_price = (mma + sc + sh) / (_ONE - fr)
    reason    = ""
    if base_raw < min_price:
        base_raw = min_price
        reason   = "min_margin_enforced"

    # ── Step 3: *.99 rounding ─────────────────────────────────────────────────
    base_rounded = _apply_rounding_usd(base_raw)

    # ── Step 4: Competitor clamping ───────────────────────────────────────────
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    recommended  = base_rounded

    if competitor_band is not None:
        lower_bound = _round2(competitor_band.min_price    * lbf)
        upper_bound = _round2(competitor_band.median_price * ubf)

        if recommended < lower_bound:
            recommended = lower_bound
            reason      = reason + (";clamped_up" if reason else "clamped_up")
        elif recommended > upper_bound:
            recommended = upper_bound
            reason      = reason + (";clamped_down" if reason else "clamped_down")

        # Final *.99 rounding after clamping (only if not from bound directly)
        # We keep the exact bound values to avoid violating them after re-rounding.
        # Just quantise to 2dp to be safe.
        recommended = _round2(recommended)

    # ── Step 5: Expected margin ───────────────────────────────────────────────
    margin_pct = _expected_margin_pct(recommended, sc, sh, fr)

    return RecommendedPrice(
        base_price          = _round2(base_raw),
        base_rounded        = base_rounded,
        recommended_price   = recommended,
        lower_bound         = lower_bound,
        upper_bound         = upper_bound,
        expected_margin_pct = margin_pct,
        reason              = reason,
        competitor_band     = competitor_band,
    )
