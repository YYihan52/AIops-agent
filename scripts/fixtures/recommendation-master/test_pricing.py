"""Regression test for pricing.py — member prices must round half-up to the
cent at the .5 boundary (price-display policy)."""
from __future__ import annotations

import pricing


def test_member_price_rounds_half_up():
    # 999 * (100 - 50) / 100 = 499.5 cents -> policy: round half-up -> 500
    got = pricing.member_price_cents("PRODUCT-2", 50)
    assert got == 500, f"member price must round half-up (expected 500, got {got})"


def test_member_price_when_no_rounding_is_needed():
    # 1999 * (100 - 10) / 100 = 1799.1 cents -> 1799 either way
    assert pricing.member_price_cents("PRODUCT-0", 10) == 1799
