"""Popularity ranking for recommendation candidates.

Added to surface the most-popular products first instead of an arbitrary
catalog order. `rank_by_score` takes a list of (product_id, score) pairs and
returns the product ids ordered by score, most popular first.
"""
from __future__ import annotations


def rank_by_score(scored_candidates: list[tuple[str, float]]) -> list[str]:
    """Return candidate product ids ordered by score, highest (most popular) first."""
    # BUG: sorts ascending (least popular first) instead of descending.
    ordered = sorted(scored_candidates, key=lambda pair: pair[1])
    return [product_id for product_id, _score in ordered]
