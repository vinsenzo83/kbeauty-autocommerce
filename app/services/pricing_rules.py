from __future__ import annotations

"""
app/services/pricing_rules.py
──────────────────────────────
Sprint 8 – Pure pricing computation functions.

All functions are stateless and side-effect-free so they can be
unit-tested in isolation without any DB or network calls.

Public API
----------
compute_price(supplier_price, shipping_cost, fee_rate, target_margin_rate,
              min_margin_abs) -> (computed_price, reason)

apply_rounding_usd(price) -> float
    Round to *.99 (e.g. 23.45 → 23.99, 24.00 → 24.99).

enforce_min_margin(computed_price, supplier_price, shipping_cost, fee_rate,
                   min_margin_abs) -> (final_price, reason)
"""

from decimal import ROUND_FLOOR, Decimal

# ─────────────────────────────────────────────────────────────────────────────
# Rounding
# ─────────────────────────────────────────────────────────────────────────────

def apply_rounding_usd(price: float | Decimal) -> Decimal:
    """
    Round to *.99 (one cent below the next integer dollar).

    Rule: floor(price) → floor_val.99

    Examples
    --------
    >>> apply_rounding_usd(23.00)
    Decimal('23.99')
    >>> apply_rounding_usd(23.45)
    Decimal('23.99')
    >>> apply_rounding_usd(23.99)
    Decimal('23.99')
    >>> apply_rounding_usd(24.01)
    Decimal('24.99')
    >>> apply_rounding_usd(0.50)
    Decimal('0.99')
    >>> apply_rounding_usd(99.01)
    Decimal('99.99')
    >>> apply_rounding_usd(100.00)
    Decimal('100.99')
    """
    p         = Decimal(str(price))
    floor_val = int(p.to_integral_value(rounding=ROUND_FLOOR))
    return Decimal(f"{floor_val}.99")


# ─────────────────────────────────────────────────────────────────────────────
# Min-margin enforcement
# ─────────────────────────────────────────────────────────────────────────────

def enforce_min_margin(
    computed_price: Decimal,
    supplier_price: float | Decimal,
    shipping_cost: float | Decimal,
    fee_rate: float | Decimal,
    min_margin_abs: float | Decimal,
) -> tuple[Decimal, str]:
    """
    Ensure the computed price yields at least *min_margin_abs* after costs.

    Gross margin = sell_price - cost - shipping - fee
    fee           = sell_price * fee_rate

    Solving for sell_price:
        sell_price - cost - shipping - sell_price * fee_rate >= min_margin_abs
        sell_price * (1 - fee_rate)                         >= min_margin_abs + cost + shipping
        sell_price                                           >= (min_margin_abs + cost + shipping) / (1 - fee_rate)

    Returns
    -------
    (final_price, reason)
        reason is 'min_margin_enforced' when the floor was applied, '' otherwise.
    """
    sp  = Decimal(str(supplier_price))
    sc  = Decimal(str(shipping_cost))
    fr  = Decimal(str(fee_rate))
    mma = Decimal(str(min_margin_abs))

    # Minimum price to clear min_margin_abs
    min_price = (mma + sp + sc) / (Decimal("1") - fr)

    if computed_price >= min_price:
        return computed_price, ""

    return min_price, "min_margin_enforced"


# ─────────────────────────────────────────────────────────────────────────────
# Main price computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_price(
    supplier_price: float | Decimal,
    shipping_cost: float | Decimal = 3.00,
    fee_rate: float | Decimal = 0.03,
    target_margin_rate: float | Decimal = 0.30,
    min_margin_abs: float | Decimal = 3.00,
) -> tuple[Decimal, Decimal, str]:
    """
    Compute the Shopify sell price from supplier cost + margin settings.

    Formula (target margin)
    -----------------------
    cost = supplier_price + shipping_cost
    sell_price = cost / (1 - target_margin_rate - fee_rate)

    Then enforce min_margin_abs, then apply *.99 rounding.

    Parameters
    ----------
    supplier_price      : Supplier's unit price (cost).
    shipping_cost       : Shipping cost to add (default $3.00).
    fee_rate            : Platform/payment fee as a rate (default 0.03 = 3 %).
    target_margin_rate  : Target gross margin rate (default 0.30 = 30 %).
    min_margin_abs      : Minimum absolute margin in USD (default $3.00).

    Returns
    -------
    (computed_price, rounded_price, reason)
        computed_price : Pre-rounding computed price (Decimal).
        rounded_price  : Final *.99 price (Decimal).
        reason         : '' or 'min_margin_enforced'.
    """
    sp  = Decimal(str(supplier_price))
    sc  = Decimal(str(shipping_cost))
    fr  = Decimal(str(fee_rate))
    tmr = Decimal(str(target_margin_rate))

    # Target-margin computation
    denominator = Decimal("1") - tmr - fr
    if denominator <= 0:
        raise ValueError(
            f"target_margin_rate ({target_margin_rate}) + fee_rate ({fee_rate}) must be < 1.0"
        )

    cost          = sp + sc
    computed_raw  = cost / denominator

    # Enforce minimum margin
    computed_price, reason = enforce_min_margin(
        computed_raw, sp, sc, fr, min_margin_abs
    )

    # Apply *.99 rounding
    rounded_price = apply_rounding_usd(computed_price)

    return computed_price, rounded_price, reason
