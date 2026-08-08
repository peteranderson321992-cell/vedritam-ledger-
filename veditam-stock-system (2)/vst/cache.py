# cache.py
# Tiny in-process TTL cache + pagination helpers.
#
# The data layer is CSV backed, so every list endpoint used to re-read and
# re-parse whole files on each request. Read paths now go through this cache
# and any write invalidates the affected key, so the data stays authoritative.

import threading
import time
from typing import Any, Dict, Optional

_LOCK = threading.RLock()
_STORE: Dict[str, Dict[str, Any]] = {}
DEFAULT_TTL = 15  # seconds

_STATS = {"hits": 0, "misses": 0, "invalidations": 0}


def get(key: str) -> Optional[Any]:
    with _LOCK:
        entry = _STORE.get(key)
        if not entry:
            _STATS["misses"] += 1
            return None
        if entry["expires"] < time.time():
            _STORE.pop(key, None)
            _STATS["misses"] += 1
            return None
        _STATS["hits"] += 1
        return entry["value"]


def set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> Any:  # noqa: A001
    with _LOCK:
        _STORE[key] = {"value": value, "expires": time.time() + max(1, ttl)}
    return value


def invalidate(key: str) -> None:
    with _LOCK:
        _STORE.pop(key, None)
        _STATS["invalidations"] += 1


def clear() -> None:
    with _LOCK:
        _STORE.clear()


def stats() -> Dict[str, Any]:
    with _LOCK:
        return {"entries": len(_STORE), **_STATS}
