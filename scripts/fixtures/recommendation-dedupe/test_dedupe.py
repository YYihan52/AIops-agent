"""Regression test for dedupe.py — a response must never list the same
product id twice."""
from __future__ import annotations

import dedupe


def test_dedupe_removes_exact_duplicates():
    out = dedupe.dedupe_ids(["PRODUCT-1", "PRODUCT-1", "PRODUCT-2"])
    assert out == ["PRODUCT-1", "PRODUCT-2"], f"duplicates leaked through: {out}"
