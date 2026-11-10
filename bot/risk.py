"""Risk manager. Every order must pass through approve() before it reaches a broker."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .config import RiskConfig


@dataclass
class OrderIntent:
    side: str            # "long" or "short"
    entry: float
    stop: float
    target: float | None = None
    atr: float | None = None     # volatility at signal time, used for trailing stops and vol sizing


@dataclass
class ApprovedOrder:
    side: str
    qty: float           # base-asset quantity
    entry: float
    stop: float
    target: float | None
    leverage: float
    notional: float
    est_liq_price: float


class RiskRejected(Exception):
    pass


def estimate_liq_price(side: str, entry: float, leverage: float, mmr_pct: float) -> float:
    """Rough isolated-margin liquidation price. Exchanges differ; treat as an estimate."""
    move = 1.0 / leverage - mmr_pct / 100.0
    return entry * (1 - move) if side == "long" else entry * (1 + move)


class RiskManager:
    def __init__(self, cfg: RiskConfig, kill_switch_file: str | Path = "KILL"):
        self.cfg = cfg
        self.kill_switch_file = Path(kill_switch_file)
        self.peak_equity: float | None = None
        self.day: date | None = None
        self.day_start_equity: float | None = None
        self.killed_reason: str | None = None

    # --- state updates -------------------------------------------------
    def on_equity(self, equity: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        if self.day != today:
            self.day = today
            self.day_start_equity = equity
        self.peak_equity = equity if self.peak_equity is None else max(self.peak_equity, equity)
        dd = 100 * (1 - equity / self.peak_equity) if self.peak_equity else 0.0
        if dd >= self.cfg.max_drawdown_pct:
            self.kill(f"max drawdown {dd:.1f}% >= {self.cfg.max_drawdown_pct}%")

    def kill(self, reason: str) -> None:
        if not self.killed_reason:
            self.killed_reason = reason

    # --- checks --------------------------------------------------------
    @property
    def kill_switch_active(self) -> bool:
        if self.kill_switch_file.exists():
            self.kill(f"kill switch file {self.kill_switch_file} present")
        return self.killed_reason is not None

    def daily_loss_pct(self, equity: float) -> float:
        if not self.day_start_equity:
            return 0.0
        return 100 * (1 - equity / self.day_start_equity)

    def can_trade(self, equity: float, open_positions: int) -> tuple[bool, str]:
        if self.kill_switch_active:
            return False, f"killed: {self.killed_reason}"
        if self.daily_loss_pct(equity) >= self.cfg.daily_loss_limit_pct:
            return False, "daily loss limit reached"
        if open_positions >= self.cfg.max_open_positions:
            return False, "max open positions"
        return True, "ok"

    def approve(self, intent: OrderIntent, equity: float, open_positions: int,
                risk_scale: float = 1.0) -> ApprovedOrder:
        """Size and check an order. risk_scale (volatility sizing) adjusts risk per trade but can never
        push it past the hard ceiling."""
        ok, why = self.can_trade(equity, open_positions)
        if not ok:
            raise RiskRejected(why)
        if intent.side not in ("long", "short"):
            raise RiskRejected(f"bad side {intent.side}")
        if intent.stop is None or intent.stop <= 0:
            raise RiskRejected("every order needs a stop-loss")
        stop_dist = intent.entry - intent.stop if intent.side == "long" else intent.stop - intent.entry
        if stop_dist <= 0:
            raise RiskRejected("stop is on the wrong side of entry")

        from .config import HARD_MAX_RISK_PER_TRADE_PCT
        risk_pct = min(self.cfg.risk_per_trade_pct * max(risk_scale, 0.0), HARD_MAX_RISK_PER_TRADE_PCT)
        risk_usd = equity * risk_pct / 100
        qty = risk_usd / stop_dist
        notional = qty * intent.entry
        max_notional = equity * self.cfg.max_leverage
        if notional > max_notional:           # leverage cap shrinks the position
            notional = max_notional
            qty = notional / intent.entry
        leverage = notional / equity

        liq = estimate_liq_price(intent.side, intent.entry, max(leverage, 1e-9), self.cfg.maintenance_margin_pct)
        liq_dist = abs(intent.entry - liq)
        if stop_dist > liq_dist * self.cfg.min_liq_buffer_pct / 100:
            raise RiskRejected(
                f"stop {intent.stop:.2f} too close to est. liquidation {liq:.2f}; lower leverage or tighten stop"
            )
        if qty <= 0:
            raise RiskRejected("position size rounds to zero")
        return ApprovedOrder(intent.side, qty, intent.entry, intent.stop, intent.target, leverage, notional, liq)
