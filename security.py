"""Request helpers shared by all routers: trustworthy client IP and bounded caches."""
import os
import time
from collections import OrderedDict

from fastapi import Request

# Number of reverse proxies in front of the app that APPEND to X-Forwarded-For.
# Render runs one proxy in front of the service, so the right-most entry is the
# address that proxy saw. Entries to the left of it are supplied by the client
# and can be forged, so they must never be used for rate limiting.
TRUSTED_PROXY_HOPS = max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))


def client_ip(request: Request) -> str:
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if TRUSTED_PROXY_HOPS and len(forwarded) >= TRUSTED_PROXY_HOPS:
        return forwarded[-TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "unknown"


class BoundedCache:
    """Small in-process LRU cache with a time-to-live, so memory cannot grow without limit."""

    def __init__(self, max_items: int = 512, ttl_seconds: float = 6 * 3600):
        self.max_items = max_items
        self.ttl = ttl_seconds
        self._data: "OrderedDict[object, tuple[float, object]]" = OrderedDict()

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        stored_at, value = item
        if time.monotonic() - stored_at > self.ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key, value):
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    def clear(self):
        self._data.clear()

    def __len__(self):
        return len(self._data)


class DailyCounter:
    """Per-key counter that resets every UTC day and keeps at most `max_keys` keys."""

    def __init__(self, max_keys: int = 20000):
        self.max_keys = max_keys
        self._day = ""
        self._counts: dict[str, int] = {}

    def _roll(self):
        today = time.strftime("%Y%m%d", time.gmtime())
        if today != self._day:
            self._day = today
            self._counts = {}

    def get(self, key: str) -> int:
        self._roll()
        return self._counts.get(key, 0)

    def increment(self, key: str) -> int:
        self._roll()
        if key not in self._counts and len(self._counts) >= self.max_keys:
            # Under a flood of distinct keys, refuse to track more instead of growing.
            return self.max_keys
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def clear(self):
        self._counts = {}
