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
class SleeveConfig:
    """One slice of the account running one strategy on one market.

    Sleeves trade different symbols so their positions never net against each other on the exchange.
    """
    name: str
    weight: float                        # share of the account, e.g. 1 and 2 for a 1:2 split
    symbol: str
    strategy: str                        # key in bot.strategy.STRATEGIES
    max_leverage: float
    risk_per_trade_pct: float            # % of this sleeve's equity per trade
    params: dict = field(default_factory=dict)


def default_sleeves() -> list[SleeveConfig]:
    return [
        SleeveConfig(
            name="safe", weight=1, symbol="BTC/USD:USD", strategy="ema_trend",
            max_leverage=2.0, risk_per_trade_pct=0.75,
            params={"fast_ema": 20, "slow_ema": 50, "trend_ema": 200,
                    "atr_period": 14, "atr_stop_mult": 2.5, "atr_target_mult": 5.0},
        ),
        SleeveConfig(
            name="aggressive", weight=2, symbol="ETH/USD:USD", strategy="breakout",
            max_leverage=4.0, risk_per_trade_pct=1.5,
            params={"lookback": 20, "atr_period": 14, "atr_stop_mult": 1.5, "atr_target_mult": 3.0},
        ),
    ]


@dataclass
class Config:
    mode: str = "paper"                  # backtest | paper | live
    exchange: str = "krakenfutures"
    timeframe: str = "1h"
    starting_equity: float = 500.0
    taker_fee_pct: float = 0.05
    funding_rate_per_8h_pct: float = 0.01
    kill_switch_file: str = "KILL"
    state_dir: str = "state"
    heartbeat_hours: float = 6.0         # phone status update interval
    risk: RiskConfig = field(default_factory=RiskConfig)      # account-wide loss limits and kill switch
    sleeves: list[SleeveConfig] = field(default_factory=default_sleeves)

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def sleeve_risk(self, s: SleeveConfig) -> RiskConfig:
        """Per-sleeve sizing limits. RiskConfig still clamps everything to the hard ceilings."""
        return RiskConfig(
            max_leverage=s.max_leverage,
            daily_loss_limit_pct=self.risk.daily_loss_limit_pct,
            max_drawdown_pct=self.risk.max_drawdown_pct,
            min_liq_buffer_pct=self.risk.min_liq_buffer_pct,
            maintenance_margin_pct=self.risk.maintenance_margin_pct,
            risk_per_trade_pct=s.risk_per_trade_pct,
            max_open_positions=1,
        )


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text())
    risk = RiskConfig(**raw.pop("risk", {}))
    sleeves = [SleeveConfig(**s) for s in raw.pop("sleeves", [])] or default_sleeves()
    cfg = Config(**raw, risk=risk, sleeves=sleeves)
    if cfg.mode not in {"backtest", "paper", "live"}:
        raise ValueError(f"unknown mode {cfg.mode!r}")
    if len({s.symbol for s in cfg.sleeves}) != len(cfg.sleeves):
        raise ValueError("each sleeve needs its own symbol")
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
