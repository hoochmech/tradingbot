"""Backtester that drives the same Portfolio (and risk checks) used for paper trading."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .broker import Trade
from .config import Config
from .portfolio import TF_HOURS, Portfolio  # noqa: F401  (TF_HOURS re-exported for callers)


@dataclass
class Result:
    equity_curve: pd.Series
    trades: dict[str, list[Trade]]
    killed_reason: str | None
    rejections: int
    groups: dict[str, str] | None = None
    missed: dict[str, int] | None = None

    def summary(self) -> dict:
        eq = self.equity_curve
        start, end = float(eq.iloc[0]), float(eq.iloc[-1])
        out = {
            "start_equity": round(start, 2),
            "end_equity": round(end, 2),
            "return_pct": round(100 * (end / start - 1), 2),
            "max_drawdown_pct": round(float((1 - eq / eq.cummax()).max() * 100), 2),
            "risk_rejections": self.rejections,
            "killed": self.killed_reason,
            "sleeves": {},
        }
        for name, trades in self.trades.items():
            wins = [t for t in trades if t.pnl > 0]
            out["sleeves"][name] = {
                "trades": len(trades),
                "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
                "net_pnl": round(sum(t.pnl for t in trades), 2),
                "fees_paid": round(sum(t.fees for t in trades), 2),
                "liquidations": sum(t.reason == "liquidated" for t in trades),
                "missed_limit_entries": (self.missed or {}).get(name, 0),
            }
        if self.groups:
            totals: dict[str, float] = {}
            for name, g in self.groups.items():
                totals[g] = totals.get(g, 0.0) + out["sleeves"][name]["net_pnl"]
            out["net_pnl_by_group"] = {g: round(v, 2) for g, v in totals.items()}
        return out


def run_backtest(data: dict[str, pd.DataFrame], cfg: Config) -> Result:
    missing = [s.symbol for s in cfg.sleeves if s.symbol not in data]
    if missing:
        print(f"no data for {', '.join(missing)}: leaving those coins out of this backtest")
        cfg = replace(cfg, sleeves=[s for s in cfg.sleeves if s.symbol in data])
    idx = None
    for s in cfg.sleeves:
        idx = data[s.symbol].index if idx is None else idx.intersection(data[s.symbol].index)
    data = {sym: df.loc[idx] for sym, df in data.items()}
    pf = Portfolio(cfg, kill_switch_file="__never__")
    curve = []
    for i in range(len(idx)):
        frames = {s.symbol: data[s.symbol].iloc[max(0, i - 600): i + 1] for s in cfg.sleeves}
        pf.step(frames, idx[i])
        curve.append(pf.equity)
    return Result(
        pd.Series(curve, index=idx),
        {s.cfg.name: s.broker.trades for s in pf.sleeves},
        pf.guard.killed_reason,
        pf.rejections,
        {s.cfg.name: s.cfg.group or s.cfg.name for s in pf.sleeves},
        {s.cfg.name: s.broker.missed_entries for s in pf.sleeves},
    )


def synthetic_ohlcv(n: int = 2000, seed: int = 0, start: float = 60000.0, vol: float = 0.006) -> pd.DataFrame:
    """Random-walk candles for tests and demos. Not real market data."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, vol, n) + 0.002 * np.sin(np.arange(n) / 150 + seed)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.r_[start, close[:-1]]
    spread = np.abs(rng.normal(0, vol / 2, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)


def synthetic_data(cfg: Config, n: int = 2000) -> dict[str, pd.DataFrame]:
    return {s.symbol: synthetic_ohlcv(n, seed=i, start=60000.0 / (i * 20 + 1), vol=0.006 + 0.002 * i)
            for i, s in enumerate(cfg.sleeves)}


def csv_name(symbol: str, timeframe: str) -> str:
    return f"{symbol.replace('/', '-').replace(':', '_')}_{timeframe}.csv"


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
