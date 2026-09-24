"""Valkey-backed cache: JSON get/set, epochs, single-flight locks, negative cache.

See idx_agent_infrastructure_diagrams_md.md sections 6-7 and
sectors_idx_ingest_cache_plan_md.md sections 3-6.
"""

import json
from contextlib import contextmanager

import redis

EPOCH_PRICE = "price_epoch"
EPOCH_FUND = "fund_epoch"

NOT_FOUND_TTL_SECONDS = 30
LOCK_TIMEOUT_SECONDS = 30
LOCK_BLOCKING_TIMEOUT_SECONDS = 10


class Cache:
    def __init__(self, host: str, port: int):
        self._redis = redis.Redis(host=host, port=port, decode_responses=True)

    @classmethod
    def from_client(cls, client: redis.Redis) -> "Cache":
        cache = cls.__new__(cls)
        cache._redis = client
        return cache

    def get(self, key: str):
        raw = self._redis.get(key)
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value, ttl: int | None = None) -> None:
        self._redis.set(key, json.dumps(value, default=str), ex=ttl)

    @contextmanager
    def acquire_lock(self, key: str):
        """Single-flight lock so concurrent misses only hit Sectors once."""
        lock = self._redis.lock(
            f"lock:{key}",
            timeout=LOCK_TIMEOUT_SECONDS,
            blocking_timeout=LOCK_BLOCKING_TIMEOUT_SECONDS,
        )
        acquired = lock.acquire(blocking=True)
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    lock.release()
                except redis.exceptions.LockNotOwnedError:
                    pass

    def get_epoch(self, name: str) -> int:
        value = self._redis.get(f"epoch:{name}")
        return int(value) if value is not None else 0

    def bump_epoch(self, name: str) -> int:
        return self._redis.incr(f"epoch:{name}")

    def get_symbol_version(self, symbol: str) -> int:
        value = self._redis.get(f"ver:idx:{symbol}")
        return int(value) if value is not None else 0

    def bump_symbol_version(self, symbol: str) -> int:
        return self._redis.incr(f"ver:idx:{symbol}")

    def incr_with_expiry(self, key: str, ttl: int) -> int:
        """Increment a counter, setting its TTL only on the first increment
        (a fixed-window rate-limit counter)."""
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, ttl)
        return count

    def hset_json(self, key: str, field: str, value) -> None:
        self._redis.hset(key, field, json.dumps(value, default=str))

    def hget_json(self, key: str, field: str):
        raw = self._redis.hget(key, field)
        return None if raw is None else json.loads(raw)

    def hgetall_json(self, key: str) -> dict:
        return {k: json.loads(v) for k, v in self._redis.hgetall(key).items()}

    def expire(self, key: str, ttl: int) -> None:
        self._redis.expire(key, ttl)

    def delete(self, *keys: str) -> None:
        if keys:
            self._redis.delete(*keys)

    def is_negative_cached(self, key: str) -> bool:
        return self._redis.exists(f"notfound:{key}") == 1

    def negative_cache(self, key: str, ttl: int = NOT_FOUND_TTL_SECONDS) -> None:
        self._redis.set(f"notfound:{key}", "1", ex=ttl)
