"""Regression test for cache.py — entries must expire after their TTL, and
still be served while fresh."""
from __future__ import annotations

import time

import cache


def test_entry_expires_after_ttl():
    c = cache.TTLCache()
    c.set("trending", ["PRODUCT-0"], ttl_seconds=0.05)
    time.sleep(0.1)
    assert c.get("trending") is None, "cache entry outlived its TTL (stale data served)"


def test_entry_fresh_within_ttl():
    c = cache.TTLCache()
    c.set("trending", ["PRODUCT-0"], ttl_seconds=5)
    assert c.get("trending") == ["PRODUCT-0"]
