"""Regression test for pagination.py — catalog pages must start at the very
first item and cover the list exactly once, with no overlap and no gaps."""
from __future__ import annotations

import pagination


def test_first_page_starts_at_the_beginning():
    items = [f"PRODUCT-{i}" for i in range(10)]
    got = pagination.paginate(items, page=1, page_size=3)
    assert got == ["PRODUCT-0", "PRODUCT-1", "PRODUCT-2"], (
        f"page 1 must start with the first item, got {got}"
    )


def test_pages_cover_all_items_without_gaps():
    items = [f"PRODUCT-{i}" for i in range(10)]
    got = []
    for page in range(1, 5):
        got.extend(pagination.paginate(items, page=page, page_size=3))
    assert got[:10] == items, f"pages must cover every item in order, got {got}"
