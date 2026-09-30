"""Environment-backed settings for the data layer."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    sectors_api_key: str
    sectors_api_base_url: str
    postgres_dsn: str
    valkey_host: str
    valkey_port: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            sectors_api_key=os.environ["SECTORS_API_KEY"],
            # `.get(key, default)` only falls back when the key is ABSENT, not when
            # it's present-but-blank — `.env.example` ships SECTORS_API_BASE_URL=
            # (blank, "everything else can stay as-is" per the README), so
            # docker-compose's env_file sets it to "" in every container, and the
            # intended default silently never applied. Real bug, reproduced from a
            # genuinely fresh clone (2026-10-01): every Sectors API call failed with
            # `httpx.UnsupportedProtocol: Request URL is missing an 'http://' or
            # 'https://' protocol.` `or` (not `.get`'s default) is what actually
            # falls through on blank.
            # data/sectors_client.py's own _get() requires a trailing "/v2/" (a
            # bare "/" here makes httpx resolve endpoint paths against the domain
            # root and silently drop the version prefix — found live: the default
            # below was originally just "https://api.sectors.app", which 404'd on
            # every real call once SECTORS_API_BASE_URL's blank-default bug above
            # was fixed and this one was finally reachable).
            sectors_api_base_url=os.environ.get("SECTORS_API_BASE_URL") or "https://api.sectors.app/v2/",
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgresql://{user}:{password}@{host}:{port}/{db}".format(
                    user=os.environ.get("POSTGRES_USER", "idx_agent"),
                    password=os.environ.get("POSTGRES_PASSWORD", ""),
                    host=os.environ.get("POSTGRES_HOST", "localhost"),
                    port=os.environ.get("POSTGRES_PORT", "5432"),
                    db=os.environ.get("POSTGRES_DB", "idx_agent"),
                ),
            ),
            valkey_host=os.environ.get("VALKEY_HOST", "localhost"),
            valkey_port=int(os.environ.get("VALKEY_PORT", "6379")),
        )
