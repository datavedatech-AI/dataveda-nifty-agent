import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Webhook security
    webhook_passphrase: str = Field(..., alias="WEBHOOK_PASSPHRASE")
    webhook_allowed_ips: str = Field(default="", alias="WEBHOOK_ALLOWED_IPS")

    # Trading mode
    trading_mode: Literal["live", "paper"] = Field(default="live", alias="TRADING_MODE")
    kill_switch: bool = Field(default=False, alias="KILL_SWITCH")

    # Brokers
    enabled_brokers: str = Field(default="dhan,mt5", alias="ENABLED_BROKERS")

    dhan_client_id: str = Field(default="", alias="DHAN_CLIENT_ID")
    dhan_access_token: str = Field(default="", alias="DHAN_ACCESS_TOKEN")
    dhan_base_url: str = Field(default="https://api.dhan.co/v2", alias="DHAN_BASE_URL")

    mt5_login: str = Field(default="", alias="MT5_LOGIN")
    mt5_password: str = Field(default="", alias="MT5_PASSWORD")
    mt5_server: str = Field(default="", alias="MT5_SERVER")
    mt5_terminal_path: str = Field(default="", alias="MT5_TERMINAL_PATH")
    mt5_mode: Literal["direct", "bridge"] = Field(default="direct", alias="MT5_MODE")
    mt5_bridge_url: str = Field(default="", alias="MT5_BRIDGE_URL")

    # Risk
    risk_max_qty_per_order: float = Field(default=75, alias="RISK_MAX_QTY_PER_ORDER")
    risk_max_open_positions: int = Field(default=5, alias="RISK_MAX_OPEN_POSITIONS")
    risk_max_daily_loss: float = Field(default=10000, alias="RISK_MAX_DAILY_LOSS")
    risk_allowed_symbols: str = Field(default="", alias="RISK_ALLOWED_SYMBOLS")

    # Symbol map
    symbol_map_file: str = Field(default="symbol_map.json", alias="SYMBOL_MAP_FILE")

    # Storage
    database_url: str = Field(default="sqlite:///./data/trades.db", alias="DATABASE_URL")

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("webhook_passphrase")
    @classmethod
    def _passphrase_not_default(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError("WEBHOOK_PASSPHRASE must be set to a non-empty secret")
        return v

    @property
    def enabled_brokers_list(self) -> list[str]:
        return [b.strip().lower() for b in self.enabled_brokers.split(",") if b.strip()]

    @property
    def webhook_allowed_ips_list(self) -> list[str]:
        return [ip.strip() for ip in self.webhook_allowed_ips.split(",") if ip.strip()]

    @property
    def risk_allowed_symbols_list(self) -> list[str]:
        return [s.strip().upper() for s in self.risk_allowed_symbols.split(",") if s.strip()]

    def load_symbol_map(self) -> dict:
        path = Path(self.symbol_map_file)
        if not path.exists():
            return {}
        with path.open() as f:
            return json.load(f)


@lru_cache
def get_settings() -> Settings:
    return Settings()
