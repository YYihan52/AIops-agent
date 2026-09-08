"""Regression test for ranking.py — recommendations must be ordered by score,
most popular (highest score) first."""
from __future__ import annotations

import ranking


def test_rank_by_score_orders_most_popular_first():
    scored = [("PRODUCT-0", 1.0), ("PRODUCT-1", 9.0), ("PRODUCT-2", 5.0)]
    ordered = ranking.rank_by_score(scored)
    assert ordered == ["PRODUCT-1", "PRODUCT-2", "PRODUCT-0"], (
        f"expected most-popular-first order, got {ordered}"
    )
