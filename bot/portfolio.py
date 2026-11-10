"""Runs several strategy sleeves side by side on one account, with account-wide risk limits on top."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .broker import PaperBroker
from .config import Config, SleeveConfig
from .risk import RiskManager, RiskRejected
from .strategy import STRATEGIES, atr

TF_HOURS = {"5m": 5 / 60, "15m": 0.25, "1h": 1, "4h": 4, "1d": 24}
WINDOW = 600  # bars of history handed to a strategy each step


@dataclass
class Sleeve:
    cfg: SleeveConfig
    broker: PaperBroker
    sizer: RiskManager
    last_price: float = 0.0

    @property
    def equity(self) -> float:
        return self.broker.equity(self.last_price) if self.last_price else self.broker.cash


@dataclass
class Portfolio:
    cfg: Config
    kill_switch_file: str = "KILL"
    sleeves: list[Sleeve] = field(default_factory=list)
    rejections: int = 0

    def __post_init__(self) -> None:
        total_w = sum(s.weight for s in self.cfg.sleeves)
        for sc in self.cfg.sleeves:
            broker = PaperBroker(
                cash=self.cfg.starting_equity * sc.weight / total_w,
                taker_fee_pct=self.cfg.taker_fee_pct,
                maker_fee_pct=self.cfg.maker_fee_pct,
                funding_per_8h_pct=self.cfg.funding_rate_per_8h_pct,
                bar_hours=TF_HOURS.get(self.cfg.timeframe, 1),
            )
            # Per-sleeve sizer only sizes trades; account-wide limits live in self.guard.
            self.sleeves.append(Sleeve(sc, broker, RiskManager(self.cfg.sleeve_risk(sc), "__unused__")))
        self.guard = RiskManager(self.cfg.risk, self.kill_switch_file)

    @property
    def equity(self) -> float:
        return sum(s.equity for s in self.sleeves)

    def close_all(self, reason: str, ts=None) -> list[str]:
        out = []
        for s in self.sleeves:
            s.broker.cancel_pending()
            if s.broker.position:
                t = s.broker.close(s.last_price, reason, ts)
                out.append(f"[{s.cfg.name}] closed {t.side} {s.cfg.symbol} ({reason}) P&L ${t.pnl:+.2f}")
        return out

    def vol_scale(self, df: pd.DataFrame, period: int) -> float:
        """Risk multiplier: below 1 when the coin is more volatile than usual, above 1 when calmer."""
        if not self.cfg.vol_sizing or len(df) < 50:
            return 1.0
        atr_pct = (atr(df, period) / df["close"]).iloc[-self.cfg.vol_lookback:]
        now, usual = float(atr_pct.iloc[-1]), float(atr_pct.median())
        if not now or np.isnan(now) or np.isnan(usual):
            return 1.0
        return float(np.clip(usual / now, self.cfg.vol_scale_min, self.cfg.vol_scale_max))

    def step(self, frames: dict[str, pd.DataFrame], ts) -> list[str]:
        """Process one closed bar per symbol. Returns human-readable events for logs and phone alerts."""
        events: list[str] = []
        for s in self.sleeves:
            df = frames.get(s.cfg.symbol)
            if df is None or df.empty:
                continue
            bar = df.iloc[-1]
            s.last_price = float(bar.close)
            had_pending = s.broker.pending is not None
            if t := s.broker.on_bar(bar.high, bar.low, bar.close, ts, open_=float(bar.open)):
                events.append(f"[{s.cfg.name}] {t.reason}: closed {t.side} {s.cfg.symbol} P&L ${t.pnl:+.2f}")
            elif had_pending and s.broker.position and s.broker.pending is None \
                    and s.broker.position.opened_at == ts:
                p = s.broker.position
                events.append(f"[{s.cfg.name}] limit filled: {p.side} {s.cfg.symbol} @ {p.entry:,.4g}, "
                              f"stop {p.stop:,.4g}")

        now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        self.guard.on_equity(self.equity, now)
        if self.guard.kill_switch_active:
            events += self.close_all("kill_switch", ts)
            return events
        ok, _ = self.guard.can_trade(self.equity, 0)

        for s in self.sleeves:
            df = frames.get(s.cfg.symbol)
            if df is None or df.empty:
                continue
            df = df.iloc[-WINDOW:]
            intent = STRATEGIES[s.cfg.strategy](df, s.cfg.params)
            if not intent:
                continue
            b = s.broker
            if b.pending and b.pending.order.side != intent.side:
                b.cancel_pending()
            if b.position and b.position.side != intent.side:
                t = b.close(s.last_price, "reverse", ts)
                events.append(f"[{s.cfg.name}] reversed out of {t.side} {s.cfg.symbol} P&L ${t.pnl:+.2f}")
            if b.busy:
                continue
            if not ok:
                self.rejections += 1
                continue
            scale = self.vol_scale(df, s.cfg.params.get("atr_period", 14))
            try:
                o = s.sizer.approve(intent, s.equity, 0, risk_scale=scale)
            except RiskRejected:
                self.rejections += 1
                continue
            trail_mult = s.cfg.params.get("trail_atr_mult")
            trail = trail_mult * intent.atr if trail_mult and intent.atr else None
            risk_usd = s.equity * min(s.cfg.risk_per_trade_pct * scale, 2.0) / 100
            if self.cfg.entry_order == "limit":
                b.place_limit(o, trail, self.cfg.limit_ttl_bars)
                verb = "limit order placed"
            else:
                b.open(o, ts, trail)
                verb = "opened"
            events.append(
                f"[{s.cfg.name}] {verb}: {o.side} {s.cfg.symbol} @ {o.entry:,.4g}, stop {o.stop:,.4g}"
                f"{' (trailing)' if trail else ''}, {o.leverage:.1f}x, risking ${risk_usd:.2f}"
            )
        return events

    def status(self) -> str:
        lines = [f"Equity ${self.equity:,.2f} (start ${self.cfg.starting_equity:,.2f})"]
        if self.guard.killed_reason:
            lines.append(f"STOPPED: {self.guard.killed_reason}")
        for s in self.sleeves:
            p = s.broker.position
            if p:
                pos = f"{p.side} @ {p.entry:,.4g}, stop {p.stop:,.4g}, P&L ${p.pnl(s.last_price):+.2f}"
            elif s.broker.pending:
                pos = f"limit {s.broker.pending.order.side} pending @ {s.broker.pending.limit:,.4g}"
            else:
                pos = "flat"
            lines.append(f"[{s.cfg.name}] {s.cfg.symbol}: ${s.equity:,.2f}, {pos}")
        return "\n".join(lines)
