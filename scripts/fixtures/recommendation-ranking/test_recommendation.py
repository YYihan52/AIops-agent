"""Regression tests for the recommendation service.

test_memory_is_bounded is the s4 capstone guard: it drives many requests and asserts
the internal tracking structure does NOT grow without bound. Against the planted-leak
version this FAILS; after 代码修复 Agent's fix (bounded deque / no cache) it PASSES — which is
exactly the build+test gate that must pass before a PR is opened (§8.2).
"""
from __future__ import annotations

import recommendation_server as rs


def test_returns_recommendations():
    out = rs.get_recommendations(["PRODUCT-0"], max_results=3)
    assert isinstance(out, list)
    assert len(out) <= 3
    assert "PRODUCT-0" not in out


def test_memory_is_bounded():
    # reset module state if the fix exposed a way to; otherwise just measure growth
    start = rs.seen_count()
    for i in range(5000):
        rs.get_recommendations([f"PRODUCT-{i % 20}"])
    growth = rs.seen_count() - start
    # A bounded implementation must not accumulate one entry per request.
    # Allow a small cap (e.g. deque maxlen) but reject unbounded growth.
    assert growth < 1000, (
        f"memory appears unbounded: tracking structure grew by {growth} "
        f"entries over 5000 requests (expected a bounded cap)"
    )
