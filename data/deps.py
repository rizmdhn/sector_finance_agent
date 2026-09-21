"""Lazily-built singletons for the data layer, shared by gateway/ and ingest/."""

from functools import lru_cache

from data.cache import Cache
from data.config import Settings
from data.db import Database
from data.rate_limit import TokenBucket
from data.sectors_client import SectorsClient


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def get_db() -> Database:
    return Database(get_settings().postgres_dsn)


@lru_cache(maxsize=1)
def get_cache() -> Cache:
    settings = get_settings()
    return Cache(settings.valkey_host, settings.valkey_port)


@lru_cache(maxsize=1)
def get_client() -> SectorsClient:
    settings = get_settings()
    return SectorsClient(
        api_key=settings.sectors_api_key,
        base_url=settings.sectors_api_base_url,
        rate_limiter=TokenBucket(capacity=5, refill_per_second=1.0),
        cache=get_cache(),
    )
