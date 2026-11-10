"""Brokers. PaperBroker simulates fills; LiveBroker sends real orders via ccxt and is off by default."""
from __future__ import annotations

from dataclasses import dataclass, field

from .risk import ApprovedOrder


@dataclass
class Position:
    side: str
    qty: float
    entry: float
    stop: float
    target: float | None
    liq: float
    opened_at: object = None

    def pnl(self, price: float) -> float:
        d = price - self.entry if self.side == "long" else self.entry - price
        return d * self.qty


@dataclass
class Trade:
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float
    fees: float
    reason: str
    opened_at: object
    closed_at: object


@dataclass
class PaperBroker:
    cash: float
    taker_fee_pct: float = 0.06
    funding_per_8h_pct: float = 0.01
    bar_hours: float = 1.0
    slippage_pct: float = 0.02
    position: Position | None = None
    trades: list[Trade] = field(default_factory=list)

    def equity(self, price: float) -> float:
        return self.cash + (self.position.pnl(price) if self.position else 0.0)

    def _fee(self, notional: float) -> float:
        return notional * self.taker_fee_pct / 100

    def open(self, o: ApprovedOrder, ts=None) -> None:
        slip = 1 + self.slippage_pct / 100 if o.side == "long" else 1 - self.slippage_pct / 100
        px = o.entry * slip
        self.cash -= self._fee(o.qty * px)
        self.position = Position(o.side, o.qty, px, o.stop, o.target, o.est_liq_price, ts)

    def close(self, price: float, reason: str, ts=None) -> Trade:
        p = self.position
        assert p is not None
        slip = 1 - self.slippage_pct / 100 if p.side == "long" else 1 + self.slippage_pct / 100
        px = price * slip
        fee = self._fee(p.qty * px)
        pnl = p.pnl(px)
        self.cash += pnl - fee
        t = Trade(p.side, p.entry, px, p.qty, pnl - fee, fee, reason, p.opened_at, ts)
        self.trades.append(t)
        self.position = None
        return t

    def on_bar(self, high: float, low: float, close: float, ts=None) -> Trade | None:
        """Apply funding and check liquidation / stop / target within the bar (worst case first)."""
        p = self.position
        if p is None:
            return None
        funding = p.qty * close * self.funding_per_8h_pct / 100 * (self.bar_hours / 8)
        self.cash += -funding if p.side == "long" else funding
        if p.side == "long":
            if low <= p.liq:
                return self.close(p.liq, "liquidated", ts)
            if low <= p.stop:
                return self.close(p.stop, "stop", ts)
            if p.target and high >= p.target:
                return self.close(p.target, "target", ts)
        else:
            if high >= p.liq:
                return self.close(p.liq, "liquidated", ts)
            if high >= p.stop:
                return self.close(p.stop, "stop", ts)
            if p.target and low <= p.target:
                return self.close(p.target, "target", ts)
        return None


class LiveBroker:
    """Thin ccxt wrapper. Not wired into the runner until the exchange is chosen and tested on paper.

    Places the entry as a market order and immediately attaches a reduce-only stop on the exchange,
    so the stop survives if the bot crashes.
    """

    def __init__(self, exchange_id: str, symbol: str, creds: dict, sandbox: bool = True):
        import ccxt

        self.ex = getattr(ccxt, exchange_id)({**creds, "enableRateLimit": True})
        if sandbox:
            self.ex.set_sandbox_mode(True)
        self.symbol = symbol

    def equity(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal["total"].get("USD") or bal["total"].get("USDC") or 0.0)

    def open(self, o: ApprovedOrder) -> None:
        side = "buy" if o.side == "long" else "sell"
        exit_side = "sell" if side == "buy" else "buy"
        self.ex.load_markets()
        qty = float(self.ex.amount_to_precision(self.symbol, o.qty))
        if qty <= 0:
            raise ValueError("order size is below the exchange minimum")
        try:
            self.ex.set_leverage(max(1, round(o.leverage)), self.symbol)
        except Exception:
            pass  # not every venue or account supports this call
        self.ex.create_order(self.symbol, "market", side, qty)
        stop = float(self.ex.price_to_precision(self.symbol, o.stop))
        # Exchange-side reduce-only stop, so it protects the position even if the bot dies.
        self.ex.create_order(
            self.symbol, "market", exit_side, qty, None, {"triggerPrice": stop, "reduceOnly": True}
        )

    def flatten(self) -> None:
        """Kill switch: cancel everything and close any open position at market."""
        self.ex.cancel_all_orders(self.symbol)
        for pos in self.ex.fetch_positions([self.symbol]):
            qty = abs(float(pos.get("contracts") or 0))
            if qty:
                side = "sell" if pos["side"] == "long" else "buy"
                self.ex.create_order(self.symbol, "market", side, qty, params={"reduceOnly": True})
