"""Starter strategy: EMA trend filter with ATR-based stops. Goes long or short.

This is a placeholder to exercise the plumbing, not a proven edge. Backtest before trusting it.
"""
from __future__ import annotations

import pandas as pd

from .config import StrategyConfig
from .risk import OrderIntent


def add_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=cfg.fast_ema, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=cfg.slow_ema, adjust=False).mean()
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / cfg.atr_period, adjust=False).mean()
    return df


def signal(df: pd.DataFrame, cfg: StrategyConfig) -> OrderIntent | None:
    """Look at the last closed bar of an indicator frame and return an entry, or None."""
    if len(df) < cfg.slow_ema + 2:
        return None
    prev, cur = df.iloc[-2], df.iloc[-1]
    crossed_up = prev.ema_fast <= prev.ema_slow and cur.ema_fast > cur.ema_slow
    crossed_dn = prev.ema_fast >= prev.ema_slow and cur.ema_fast < cur.ema_slow
    entry, atr = float(cur.close), float(cur.atr)
    if crossed_up:
        return OrderIntent("long", entry, entry - cfg.atr_stop_mult * atr, entry + cfg.atr_target_mult * atr)
    if crossed_dn:
        return OrderIntent("short", entry, entry + cfg.atr_stop_mult * atr, entry - cfg.atr_target_mult * atr)
    return None
