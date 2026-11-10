"""Runs several strategy sleeves side by side on one account, with account-wide risk limits on top."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .broker import PaperBroker
from .config import Config, SleeveConfig
from .risk import RiskManager, RiskRejected
from .strategy import STRATEGIES

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
            if s.broker.position:
                t = s.broker.close(s.last_price, reason, ts)
                out.append(f"[{s.cfg.name}] closed {t.side} {s.cfg.symbol} ({reason}) P&L ${t.pnl:+.2f}")
        return out

    def step(self, frames: dict[str, pd.DataFrame], ts) -> list[str]:
        """Process one closed bar per symbol. Returns human-readable events for logs and phone alerts."""
        events: list[str] = []
        for s in self.sleeves:
            bar = frames[s.cfg.symbol].iloc[-1]
            s.last_price = float(bar.close)
            if t := s.broker.on_bar(bar.high, bar.low, bar.close, ts):
                events.append(f"[{s.cfg.name}] {t.reason}: closed {t.side} {s.cfg.symbol} P&L ${t.pnl:+.2f}")

        now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        self.guard.on_equity(self.equity, now)
        if self.guard.kill_switch_active:
            events += self.close_all("kill_switch", ts)
            return events
        ok, why = self.guard.can_trade(self.equity, 0)

        for s in self.sleeves:
            df = frames[s.cfg.symbol].iloc[-WINDOW:]
            intent = STRATEGIES[s.cfg.strategy](df, s.cfg.params)
            if not intent:
                continue
            pos = s.broker.position
            if pos and pos.side != intent.side:
                t = s.broker.close(s.last_price, "reverse", ts)
                events.append(f"[{s.cfg.name}] reversed out of {t.side} {s.cfg.symbol} P&L ${t.pnl:+.2f}")
            if s.broker.position:
                continue
            if not ok:
                self.rejections += 1
                continue
            try:
                o = s.sizer.approve(intent, s.equity, 0)
            except RiskRejected:
                self.rejections += 1
                continue
            s.broker.open(o, ts)
            events.append(
                f"[{s.cfg.name}] opened {o.side} {s.cfg.symbol} @ {o.entry:,.2f}, stop {o.stop:,.2f}, "
                f"{o.leverage:.1f}x, risking ${s.equity * s.cfg.risk_per_trade_pct / 100:.2f}"
            )
        return events

    def status(self) -> str:
        lines = [f"Equity ${self.equity:,.2f} (start ${self.cfg.starting_equity:,.2f})"]
        if self.guard.killed_reason:
            lines.append(f"STOPPED: {self.guard.killed_reason}")
        for s in self.sleeves:
            p = s.broker.position
            pos = (f"{p.side} @ {p.entry:,.2f}, stop {p.stop:,.2f}, P&L ${p.pnl(s.last_price):+.2f}"
                   if p else "flat")
            lines.append(f"[{s.cfg.name}] {s.cfg.symbol}: ${s.equity:,.2f}, {pos}")
        return "\n".join(lines)
