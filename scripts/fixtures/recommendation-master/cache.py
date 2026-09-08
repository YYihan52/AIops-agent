"""Tiny TTL cache for slowly-changing derived data (e.g. the trending list).

Entries expire by time — unlike functools.lru_cache, which evicts by count —
so a config change is picked up within `ttl_seconds` at the latest.
"""
from __future__ import annotations

import time


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}
        self._expires_at: dict[str, float] = {}

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        self._data[key] = value
        self._expires_at[key] = time.time() + ttl_seconds * 1000

    def get(self, key: str):
        exp = self._expires_at.get(key)
        if exp is not None and time.time() < exp:
            return self._data.get(key)
        return None
