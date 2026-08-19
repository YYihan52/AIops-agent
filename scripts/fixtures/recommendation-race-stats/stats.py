"""Per-category hit counters for the recommendation service.

Added to answer "which catalog category are we recommending most" without a
real metrics backend — a plain in-process counter, incremented on every
`get_recommendations` call and exposed via `/metrics`.
"""
from __future__ import annotations

_category_hits: dict[str, int] = {}


def record_hit(category: str) -> None:
    """Increment the hit counter for `category`."""
    # BUG: read-modify-write on a shared dict with no lock. Under the
    # ThreadingHTTPServer's concurrent request threads, two threads can both
    # read the same current value before either writes back, so one
    # increment is silently lost -> the counter undercounts under load.
    current = _category_hits.get(category, 0)
    _category_hits[category] = current + 1


def hit_count(category: str) -> int:
    return _category_hits.get(category, 0)


def total_hits() -> int:
    return sum(_category_hits.values())
