"""Configuration loading. Secrets come only from environment variables, never from files in the repo."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Absolute ceilings. The config file can lower these but never raise them.
HARD_MAX_LEVERAGE = 5.0
HARD_MAX_RISK_PER_TRADE_PCT = 2.0
HARD_MAX_DAILY_LOSS_PCT = 5.0


@dataclass
class RiskConfig:
    max_leverage: float = 3.0
    risk_per_trade_pct: float = 1.0      # % of equity lost if the stop is hit
    daily_loss_limit_pct: float = 3.0    # stop opening trades for the UTC day after this
    max_drawdown_pct: float = 15.0       # trips the kill switch permanently
    max_open_positions: int = 1
    min_liq_buffer_pct: float = 50.0     # stop must sit within this % of the distance to liquidation
    maintenance_margin_pct: float = 1.0  # approximate, used for liquidation estimates

    def __post_init__(self) -> None:
        self.max_leverage = min(self.max_leverage, HARD_MAX_LEVERAGE)
        self.risk_per_trade_pct = min(self.risk_per_trade_pct, HARD_MAX_RISK_PER_TRADE_PCT)
        self.daily_loss_limit_pct = min(self.daily_loss_limit_pct, HARD_MAX_DAILY_LOSS_PCT)
        if self.max_leverage <= 0 or self.risk_per_trade_pct <= 0:
            raise ValueError("leverage and risk_per_trade_pct must be positive")


@dataclass
class StrategyConfig:
    name: str = "ema_trend"
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 4.0


@dataclass
class Config:
    mode: str = "paper"                  # backtest | paper | live
    exchange: str = "krakenfutures"
    symbol: str = "BTC/USD:USD"
    timeframe: str = "1h"
    starting_equity: float = 500.0
    taker_fee_pct: float = 0.05
    funding_rate_per_8h_pct: float = 0.01
    kill_switch_file: str = "KILL"
    state_dir: str = "state"
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    @property
    def live(self) -> bool:
        return self.mode == "live"


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text())
    risk = RiskConfig(**raw.pop("risk", {}))
    strat = StrategyConfig(**raw.pop("strategy", {}))
    cfg = Config(**raw, risk=risk, strategy=strat)
    if cfg.mode not in {"backtest", "paper", "live"}:
        raise ValueError(f"unknown mode {cfg.mode!r}")
    return cfg


def api_credentials() -> dict[str, str]:
    """Read exchange keys from the environment. Use trade-only keys with withdrawals disabled."""
    key = os.environ.get("EXCHANGE_API_KEY")
    secret = os.environ.get("EXCHANGE_API_SECRET")
    if not key or not secret:
        raise RuntimeError("EXCHANGE_API_KEY / EXCHANGE_API_SECRET are not set")
    creds = {"apiKey": key, "secret": secret}
    if pw := os.environ.get("EXCHANGE_API_PASSPHRASE"):
        creds["password"] = pw
    return creds
