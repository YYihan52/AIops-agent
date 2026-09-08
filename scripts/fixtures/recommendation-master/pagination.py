"""Paginated browsing for the product catalog.

Added so the /catalog endpoint can serve the (growing) catalog in fixed-size
pages instead of one big list.
"""
from __future__ import annotations


def paginate(items: list[str], page: int, page_size: int) -> list[str]:
    """Return the `page`-th (1-based) slice of `items`, `page_size` entries per page."""
    start = page * page_size
    return items[start : start + page_size]
