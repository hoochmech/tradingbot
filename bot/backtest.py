"""Bar-by-bar backtester that uses the same RiskManager and PaperBroker as paper trading."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .broker import PaperBroker, Trade
from .config import Config
from .risk import RiskManager, RiskRejected
from .strategy import add_indicators, signal

TF_HOURS = {"5m": 5 / 60, "15m": 0.25, "1h": 1, "4h": 4, "1d": 24}


@dataclass
class Result:
    equity_curve: pd.Series
    trades: list[Trade]
    killed_reason: str | None
    rejections: int

    def summary(self) -> dict:
        eq = self.equity_curve
        start, end = float(eq.iloc[0]), float(eq.iloc[-1])
        dd = float((1 - eq / eq.cummax()).max() * 100)
        wins = [t for t in self.trades if t.pnl > 0]
        return {
            "start_equity": round(start, 2),
            "end_equity": round(end, 2),
            "return_pct": round(100 * (end / start - 1), 2),
            "max_drawdown_pct": round(dd, 2),
            "trades": len(self.trades),
            "win_rate_pct": round(100 * len(wins) / len(self.trades), 1) if self.trades else 0.0,
            "fees_paid": round(sum(t.fees for t in self.trades), 2),
            "liquidations": sum(t.reason == "liquidated" for t in self.trades),
            "risk_rejections": self.rejections,
            "killed": self.killed_reason,
        }


def run_backtest(df: pd.DataFrame, cfg: Config) -> Result:
    df = add_indicators(df, cfg.strategy)
    risk = RiskManager(cfg.risk, kill_switch_file="__never__")
    broker = PaperBroker(
        cash=cfg.starting_equity,
        taker_fee_pct=cfg.taker_fee_pct,
        funding_per_8h_pct=cfg.funding_rate_per_8h_pct,
        bar_hours=TF_HOURS.get(cfg.timeframe, 1),
    )
    curve, rejections = [], 0
    for i in range(len(df)):
        bar = df.iloc[i]
        ts = df.index[i]
        broker.on_bar(bar.high, bar.low, bar.close, ts)
        equity = broker.equity(bar.close)
        risk.on_equity(equity, ts.to_pydatetime())
        if risk.killed_reason and broker.position:
            broker.close(bar.close, "kill_switch", ts)
        intent = signal(df.iloc[: i + 1], cfg.strategy)
        if intent:
            if broker.position and broker.position.side != intent.side:
                broker.close(bar.close, "reverse", ts)
            if not broker.position:
                try:
                    o = risk.approve(intent, broker.equity(bar.close), 0)
                    broker.open(o, ts)
                except RiskRejected:
                    rejections += 1
        curve.append(broker.equity(bar.close))
    return Result(pd.Series(curve, index=df.index), broker.trades, risk.killed_reason, rejections)


def synthetic_ohlcv(n: int = 2000, seed: int = 0, start: float = 60000.0) -> pd.DataFrame:
    """Random-walk candles for tests and demos. Not real market data."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, 0.006, n) + 0.002 * np.sin(np.arange(n) / 150)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.r_[start, close[:-1]]
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
