from __future__ import annotations

"""
tests/test_sprint8_pricing_rules.py
─────────────────────────────────────
Sprint 8 – pure unit tests for pricing_rules.py (no DB, no network).

Coverage
--------
1.  apply_rounding_usd – basic cases
2.  apply_rounding_usd – already *.99
3.  apply_rounding_usd – value just above integer
4.  apply_rounding_usd – small value (< $1)
5.  enforce_min_margin – no enforcement needed
6.  enforce_min_margin – enforcement triggered
7.  compute_price – standard case
8.  compute_price – min_margin kicks in
9.  compute_price – result is always *.99
10. compute_price – raises on bad rate combination
11. compute_price – shipping_cost=0
12. compute_price – high margin target
"""

from decimal import Decimal

import pytest

from app.services.pricing_rules import (
    apply_rounding_usd,
    compute_price,
    enforce_min_margin,
)


# ─────────────────────────────────────────────────────────────────────────────
# apply_rounding_usd
# ─────────────────────────────────────────────────────────────────────────────

def test_rounding_integer_price():
    assert apply_rounding_usd(23.00) == Decimal("23.99")


def test_rounding_mid_price():
    assert apply_rounding_usd(23.45) == Decimal("23.99")


def test_rounding_already_99():
    assert apply_rounding_usd(23.99) == Decimal("23.99")


def test_rounding_just_above_integer():
    assert apply_rounding_usd(24.01) == Decimal("24.99")


def test_rounding_small_value():
    assert apply_rounding_usd(0.50) == Decimal("0.99")


def test_rounding_large_value():
    # 99.01 → 99.99
    assert apply_rounding_usd(99.01) == Decimal("99.99")


def test_rounding_exact_boundary():
    # 100.00 → 100.99
    assert apply_rounding_usd(100.00) == Decimal("100.99")


# ─────────────────────────────────────────────────────────────────────────────
# enforce_min_margin
# ─────────────────────────────────────────────────────────────────────────────

def test_enforce_min_margin_no_enforcement():
    # sell_price=30, cost=10, shipping=3, fee=0.03, min_margin=3
    # gross = 30 - 10 - 3 - 30*0.03 = 30 - 10 - 3 - 0.9 = 16.1 >> 3
    price, reason = enforce_min_margin(
        computed_price = Decimal("30.00"),
        supplier_price = Decimal("10.00"),
        shipping_cost  = Decimal("3.00"),
        fee_rate       = Decimal("0.03"),
        min_margin_abs = Decimal("3.00"),
    )
    assert reason == ""
    assert price == Decimal("30.00")


def test_enforce_min_margin_triggers():
    # sell_price=10, cost=9, shipping=3 → total cost=12 > sell_price=10
    # Should enforce minimum
    price, reason = enforce_min_margin(
        computed_price = Decimal("10.00"),
        supplier_price = Decimal("9.00"),
        shipping_cost  = Decimal("3.00"),
        fee_rate       = Decimal("0.03"),
        min_margin_abs = Decimal("3.00"),
    )
    assert reason == "min_margin_enforced"
    # Verify the min price formula: (3 + 9 + 3) / (1 - 0.03) ≈ 15.46
    assert price > Decimal("15.00")


# ─────────────────────────────────────────────────────────────────────────────
# compute_price
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_price_standard():
    # supplier=10, shipping=3, fee=0.03, margin=0.30
    # cost = 13
    # sell = 13 / (1 - 0.30 - 0.03) = 13 / 0.67 ≈ 19.40
    computed, rounded, reason = compute_price(
        supplier_price     = 10.00,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        min_margin_abs     = 3.00,
    )
    assert rounded == Decimal("19.99")
    assert reason == ""


def test_compute_price_min_margin_kicks_in():
    # Supplier price is very low, but min_margin enforced
    computed, rounded, reason = compute_price(
        supplier_price     = 0.50,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        min_margin_abs     = 10.00,  # large min margin
    )
    assert reason == "min_margin_enforced"
    # rounded_price must be *.99
    assert str(rounded).endswith(".99")


def test_compute_price_always_99_cents():
    for supplier_price in [5.00, 7.50, 10.00, 15.00, 25.00, 50.00]:
        _, rounded, _ = compute_price(
            supplier_price=supplier_price,
            shipping_cost=3.00,
            fee_rate=0.03,
            target_margin_rate=0.30,
            min_margin_abs=3.00,
        )
        assert str(rounded).endswith(".99"), (
            f"Expected *.99 for supplier_price={supplier_price}, got {rounded}"
        )


def test_compute_price_raises_on_bad_rates():
    with pytest.raises(ValueError):
        compute_price(
            supplier_price     = 10.00,
            fee_rate           = 0.60,
            target_margin_rate = 0.50,  # 0.60 + 0.50 = 1.10 > 1.0
        )


def test_compute_price_zero_shipping():
    _, rounded, _ = compute_price(
        supplier_price     = 10.00,
        shipping_cost      = 0.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.30,
        min_margin_abs     = 3.00,
    )
    assert str(rounded).endswith(".99")


def test_compute_price_high_margin():
    # 50 % margin target
    _, rounded, reason = compute_price(
        supplier_price     = 10.00,
        shipping_cost      = 3.00,
        fee_rate           = 0.03,
        target_margin_rate = 0.50,
        min_margin_abs     = 3.00,
    )
    # cost=13, sell = 13/(1-0.5-0.03) = 13/0.47 ≈ 27.66 → 27.99
    assert rounded == Decimal("27.99")
    assert reason == ""
