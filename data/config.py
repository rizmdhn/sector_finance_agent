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
            sectors_api_base_url=os.environ.get(
                "SECTORS_API_BASE_URL", "https://api.sectors.app"
            ),
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
