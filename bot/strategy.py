"""Strategies. Each takes a candle frame plus params and returns an OrderIntent for the last closed bar, or None.

- ema_trend: trades EMA crossovers, optionally only in the direction of the long-term trend.
- breakout: enters when price breaks the recent high/low range, optionally only in the direction of the
  long-term trend (trend_ema).

Both aim for a 2:1 reward-to-risk target. Neither is a proven edge. Backtest on real data before trusting them.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from .risk import OrderIntent


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _intent(side: str, entry: float, a: float, p: dict) -> OrderIntent:
    s, t = p["atr_stop_mult"] * a, p["atr_target_mult"] * a
    if side == "long":
        return OrderIntent("long", entry, entry - s, entry + t)
    return OrderIntent("short", entry, entry + s, entry - t)


def ema_trend(df: pd.DataFrame, p: dict) -> OrderIntent | None:
    need = max(p["slow_ema"], p.get("trend_ema", 0)) + 2
    if len(df) < need:
        return None
    c = df["close"]
    fast = c.ewm(span=p["fast_ema"], adjust=False).mean()
    slow = c.ewm(span=p["slow_ema"], adjust=False).mean()
    trend = c.ewm(span=p.get("trend_ema", p["slow_ema"]), adjust=False).mean()
    a = float(atr(df, p["atr_period"]).iloc[-1])
    entry = float(c.iloc[-1])
    up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
    dn = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]
    if up and entry > trend.iloc[-1]:
        return _intent("long", entry, a, p)
    if dn and entry < trend.iloc[-1]:
        return _intent("short", entry, a, p)
    return None


def breakout(df: pd.DataFrame, p: dict) -> OrderIntent | None:
    n = p["lookback"]
    if len(df) < max(n, p["atr_period"]) + 2:
        return None
    prior = df.iloc[-n - 1:-1]
    cur = df.iloc[-1]
    a = float(atr(df, p["atr_period"]).iloc[-1])
    trend = None
    if p.get("trend_ema"):
        trend = float(df["close"].ewm(span=p["trend_ema"], adjust=False).mean().iloc[-1])
    if cur.close > prior["high"].max() and (trend is None or cur.close > trend):
        return _intent("long", float(cur.close), a, p)
    if cur.close < prior["low"].min() and (trend is None or cur.close < trend):
        return _intent("short", float(cur.close), a, p)
    return None


STRATEGIES: dict[str, Callable[[pd.DataFrame, dict], OrderIntent | None]] = {
    "ema_trend": ema_trend,
    "breakout": breakout,
}
