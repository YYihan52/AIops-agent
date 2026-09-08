"""Regression test for stats.py — concurrent hit recording must not lose
increments. Drives many threads hammering record_hit() at once; against an
unsynchronized read-modify-write this reliably undercounts under CPython's
GIL thread switching, and passes once the increment is made atomic (e.g. a
threading.Lock around the read-modify-write).

Several rounds are run because a single lucky round can, by scheduling
chance, complete without losing an increment — losing none across ALL rounds
is what a correct (atomic) implementation guarantees.
"""
from __future__ import annotations

import sys
import threading

import stats


def test_record_hit_is_thread_safe():
    # Force frequent GIL switches so the read-modify-write race actually
    # interleaves within this short test, instead of relying on scheduling luck.
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    threads_n = 50
    increments_per_thread = 200
    try:
        for round_no in range(3):
            category = f"round-{round_no}"
            expected_total = threads_n * increments_per_thread

            def worker():
                for _ in range(increments_per_thread):
                    stats.record_hit(category)

            threads = [threading.Thread(target=worker) for _ in range(threads_n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            got = stats.hit_count(category)
            assert got == expected_total, (
                f"lost increments under concurrent load in round {round_no}: "
                f"expected {expected_total}, got {got} (race on the shared counter)"
            )
    finally:
        sys.setswitchinterval(old_interval)
