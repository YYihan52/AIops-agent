"""Dedupe helper for recommendation candidate ids.

Added so a single response never lists the same product twice, even if the
candidate step produced overlapping ids from multiple sources (catalog match
+ merchandising-curated trending).
"""
from __future__ import annotations


def dedupe_ids(product_ids: list[str]) -> list[str]:
    """Return product_ids with duplicates removed, preserving order.

    Ids are compared case-insensitively: "PRODUCT-1" and "product-1" are the
    same product and must not both appear in one response.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pid in product_ids:
        key = pid
        if key.lower() not in seen:
            seen.add(key)
            out.append(pid)
    return out
