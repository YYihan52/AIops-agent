"""Dedupe helper for recommendation candidate ids.

Added so a single response never lists the same product twice, even if the
scoring step produced overlapping candidates from multiple sources (catalog +
personalized model).
"""
from __future__ import annotations


def dedupe_ids(product_ids: list[str]) -> list[str]:
    """Return product_ids with exact duplicates removed, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for pid in product_ids:
        # BUG: normalizes case before the membership check but stores the
        # original casing, so "PRODUCT-1" and "product-1" both get appended
        # (the seen-set thinks they're new every time because the key stored
        # in `seen` doesn't match the lowercased lookup key).
        key = pid
        if key.lower() not in seen:
            seen.add(key)
            out.append(pid)
    return out
