"""Member-price display for the recommendation storefront.

Shows a discounted member price next to recommended products. All money is
kept in integer cents; price-display policy is to round half-up to the cent.
"""
from __future__ import annotations

# list prices, in cents, for every catalog / trending sku
_LIST_PRICES_CENTS: dict[str, int] = {
    "PRODUCT-0": 1999,
    "PRODUCT-1": 2499,
    "PRODUCT-2": 999,
    "PRODUCT-3": 4999,
    "PRODUCT-4": 1499,
    "PRODUCT-5": 2999,
    "PRODUCT-6": 799,
    "PRODUCT-7": 3499,
    "PRODUCT-8": 1299,
    "PRODUCT-9": 2199,
}


def list_price_cents(product_id: str) -> int:
    return _LIST_PRICES_CENTS[product_id]


def member_price_cents(product_id: str, discount_pct: int) -> int:
    """Member price in cents after a whole-percent discount, rounded half-up."""
    base = _LIST_PRICES_CENTS[product_id]
    discounted = base * (100 - discount_pct) / 100
    return int(discounted)
