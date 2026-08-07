"""Application configuration and the live-trading safety interlock.

Settings come from environment variables (or a ``.env`` file) via
pydantic-settings. The one piece of non-obvious behaviour here is deliberate:
``TRADEIT_TRADING_MODE=live`` is not sufficient to enable live trading. It also
requires an authorization file whose contents match a specific phrase, on the
theory that a stray environment variable in a CI job should never be able to
send real orders. See ADR-0004.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Self

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tradeit.core.enums import TradingMode
from tradeit.errors import ConfigError

#: The file that must exist, and the exact text it must contain, before the
#: platform will construct a live-trading configuration.
LIVE_AUTHORIZATION_PATH = Path("~/.tradeit/LIVE_TRADING_AUTHORIZED").expanduser()
LIVE_AUTHORIZATION_PHRASE = "I authorize tradeit to place real orders"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRADEIT_DB_", extra="ignore")

    dsn: PostgresDsn = Field(
        default=PostgresDsn("postgresql+psycopg://tradeit:tradeit@localhost:5432/tradeit")
    )
    pool_size: int = Field(default=5, ge=1, le=64)
    max_overflow: int = Field(default=10, ge=0, le=64)
    statement_timeout_s: int = Field(default=60, ge=1)
    echo_sql: bool = False


class CacheSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRADEIT_REDIS_", extra="ignore")

    dsn: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    enabled: bool = True


class DataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRADEIT_DATA_", extra="ignore")

    #: Registered provider name used for price bars (see tradeit.data.registry).
    price_provider: str = "synthetic"
    #: Provider used for fundamentals and corporate events.
    fundamental_provider: str = "synthetic"
    #: How long after a session close a daily bar is treated as knowable. Guards
    #: against a backtest acting on today's close at today's close.
    daily_bar_publication_lag_minutes: int = Field(default=20, ge=0, le=1440)
    #: Fallback assumption for filings with no reported publication timestamp.
    #: Only applied when knowledge_source would otherwise be unknown, and the
    #: resulting rows are stamped ESTIMATED so reports can flag them.
    assumed_filing_lag_days: int = Field(default=45, ge=0, le=365)
    exchange_calendar: str = "XNYS"


class Settings(BaseSettings):
    """Top-level configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TRADEIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: str = Field(default="development")
    trading_mode: TradingMode = TradingMode.PAPER
    log_level: str = Field(default="INFO")
    log_json: bool = False
    data_dir: Path = Field(default=Path("./data"))

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    data: DataSettings = Field(default_factory=DataSettings)

    @model_validator(mode="after")
    def _enforce_live_interlock(self) -> Self:
        if self.trading_mode is not TradingMode.LIVE:
            return self
        if not LIVE_AUTHORIZATION_PATH.exists():
            raise ConfigError(
                "trading_mode=live requires the authorization file at "
                f"{LIVE_AUTHORIZATION_PATH}, which does not exist. Live trading "
                "stays disabled until it is explicitly authorized."
            )
        content = LIVE_AUTHORIZATION_PATH.read_text(encoding="utf-8").strip()
        if content != LIVE_AUTHORIZATION_PHRASE:
            raise ConfigError(
                f"{LIVE_AUTHORIZATION_PATH} exists but does not contain the required "
                "authorization phrase; refusing to enable live trading."
            )
        return self

    @property
    def is_live(self) -> bool:
        return self.trading_mode is TradingMode.LIVE

    @property
    def orders_are_simulated(self) -> bool:
        return self.trading_mode in (TradingMode.BACKTEST, TradingMode.PAPER)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that configuration is read once and cannot drift mid-run. Tests
    that need different settings call ``get_settings.cache_clear()``.
    """
    return Settings()
