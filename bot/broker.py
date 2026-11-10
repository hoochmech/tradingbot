"""Brokers. PaperBroker simulates fills; LiveBroker sends real orders via ccxt and is off by default.

Order handling (both brokers):
- Entries can be limit orders (cheaper maker fee, may not fill) or market orders (taker fee, always fills).
- Every position has a protective stop. With a trailing stop, it ratchets behind the best price reached
  and never moves backwards.
- Stops exit at market (taker fee + slippage). Profit targets, when used, exit as limit orders (maker fee).
"""
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
    trail_dist: float | None = None      # absolute price distance for the trailing stop
    best: float | None = None            # best price since entry (high for longs, low for shorts)
    entry_fee: float = 0.0

    def pnl(self, price: float) -> float:
        d = price - self.entry if self.side == "long" else self.entry - price
        return d * self.qty

    def trail(self, high: float, low: float) -> None:
        if self.trail_dist is None:
            return
        if self.side == "long":
            self.best = max(self.best or high, high)
            self.stop = max(self.stop, self.best - self.trail_dist)
        else:
            self.best = min(self.best or low, low)
            self.stop = min(self.stop, self.best + self.trail_dist)


@dataclass
class PendingEntry:
    order: ApprovedOrder
    limit: float
    trail_dist: float | None
    bars_left: int


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
    taker_fee_pct: float = 0.05
    funding_per_8h_pct: float = 0.01
    bar_hours: float = 1.0
    slippage_pct: float = 0.02
    maker_fee_pct: float = 0.02
    limit_through_pct: float = 0.02      # price must trade this far through a limit to count as filled
    position: Position | None = None
    pending: PendingEntry | None = None
    trades: list[Trade] = field(default_factory=list)
    missed_entries: int = 0

    def equity(self, price: float) -> float:
        return self.cash + (self.position.pnl(price) if self.position else 0.0)

    @property
    def busy(self) -> bool:
        return self.position is not None or self.pending is not None

    def _fill(self, o: ApprovedOrder, px: float, fee_pct: float, trail_dist, ts) -> None:
        fee = o.qty * px * fee_pct / 100
        self.cash -= fee
        self.position = Position(o.side, o.qty, px, o.stop, o.target, o.est_liq_price, ts,
                                 trail_dist, px, fee)

    def open(self, o: ApprovedOrder, ts=None, trail_dist: float | None = None) -> None:
        """Market entry: always fills, pays taker fee and slippage."""
        slip = 1 + self.slippage_pct / 100 if o.side == "long" else 1 - self.slippage_pct / 100
        self._fill(o, o.entry * slip, self.taker_fee_pct, trail_dist, ts)

    def place_limit(self, o: ApprovedOrder, trail_dist: float | None = None, ttl_bars: int = 1) -> None:
        """Limit entry at the signal price. Fills on a later bar only if price trades back to it."""
        self.pending = PendingEntry(o, o.entry, trail_dist, ttl_bars)

    def cancel_pending(self) -> None:
        self.pending = None

    def close(self, price: float, reason: str, ts=None, maker: bool = False) -> Trade:
        p = self.position
        assert p is not None
        if maker:
            px, fee_pct = price, self.maker_fee_pct
        else:
            slip = 1 - self.slippage_pct / 100 if p.side == "long" else 1 + self.slippage_pct / 100
            px, fee_pct = price * slip, self.taker_fee_pct
        fee = p.qty * px * fee_pct / 100
        pnl = p.pnl(px)
        self.cash += pnl - fee
        t = Trade(p.side, p.entry, px, p.qty, pnl - fee - p.entry_fee, fee + p.entry_fee, reason,
                  p.opened_at, ts)
        self.trades.append(t)
        self.position = None
        return t

    def _try_fill_pending(self, open_: float, high: float, low: float, ts) -> None:
        pe = self.pending
        if pe is None:
            return
        o = pe.order
        through = pe.limit * self.limit_through_pct / 100
        if o.side == "long" and low <= pe.limit - through:
            self._fill(o, min(open_, pe.limit), self.maker_fee_pct, pe.trail_dist, ts)
            self.pending = None
        elif o.side == "short" and high >= pe.limit + through:
            self._fill(o, max(open_, pe.limit), self.maker_fee_pct, pe.trail_dist, ts)
            self.pending = None
        else:
            pe.bars_left -= 1
            if pe.bars_left <= 0:
                self.pending = None
                self.missed_entries += 1

    def on_bar(self, high: float, low: float, close: float, ts=None, open_: float | None = None) -> Trade | None:
        """Fill pending limits, apply funding, check liquidation / stop / target (worst case first),
        then ratchet the trailing stop for the next bar."""
        self._try_fill_pending(close if open_ is None else open_, high, low, ts)
        p = self.position
        if p is None:
            return None
        funding = p.qty * close * self.funding_per_8h_pct / 100 * (self.bar_hours / 8)
        self.cash += -funding if p.side == "long" else funding
        if p.side == "long":
            if low <= p.liq:
                return self.close(p.liq, "liquidated", ts)
            if low <= p.stop:
                return self.close(p.stop, "trailing_stop" if p.stop > p.entry else "stop", ts)
            if p.target and high >= p.target:
                return self.close(p.target, "target", ts, maker=True)
        else:
            if high >= p.liq:
                return self.close(p.liq, "liquidated", ts)
            if high >= p.stop:
                return self.close(p.stop, "trailing_stop" if p.stop < p.entry else "stop", ts)
            if p.target and low <= p.target:
                return self.close(p.target, "target", ts, maker=True)
        p.trail(high, low)
        return None


class LiveBroker:
    """Thin ccxt wrapper for one symbol. Not wired into the runner until it has been tested on
    Kraken's demo exchange.

    Entries go in as post-only limit orders. Once filled, a reduce-only stop is placed on the exchange so
    it protects the position even if the bot crashes; update_stop() moves it as the trailing stop ratchets.
    """

    def __init__(self, exchange_id: str, symbol: str, creds: dict, sandbox: bool = True):
        import ccxt

        self.ex = getattr(ccxt, exchange_id)({**creds, "enableRateLimit": True})
        if sandbox:
            self.ex.set_sandbox_mode(True)
        self.ex.load_markets()
        self.symbol = symbol
        self.stop_order_id: str | None = None

    def equity(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal["total"].get("USD") or bal["total"].get("USDC") or 0.0)

    def _qty(self, qty: float) -> float:
        q = float(self.ex.amount_to_precision(self.symbol, qty))
        if q <= 0:
            raise ValueError("order size is below the exchange minimum")
        return q

    def place_limit(self, o: ApprovedOrder) -> str:
        side = "buy" if o.side == "long" else "sell"
        try:
            self.ex.set_leverage(max(1, round(o.leverage)), self.symbol)
        except Exception:
            pass  # not every venue or account supports this call
        px = float(self.ex.price_to_precision(self.symbol, o.entry))
        order = self.ex.create_order(self.symbol, "limit", side, self._qty(o.qty), px, {"postOnly": True})
        return order["id"]

    def open_market(self, o: ApprovedOrder) -> None:
        side = "buy" if o.side == "long" else "sell"
        self.ex.create_order(self.symbol, "market", side, self._qty(o.qty))

    def place_stop(self, side: str, qty: float, stop: float) -> None:
        exit_side = "sell" if side == "long" else "buy"
        px = float(self.ex.price_to_precision(self.symbol, stop))
        order = self.ex.create_order(self.symbol, "market", exit_side, self._qty(qty), None,
                                     {"triggerPrice": px, "reduceOnly": True})
        self.stop_order_id = order["id"]

    def update_stop(self, side: str, qty: float, new_stop: float) -> None:
        """Trailing stop: place the new stop first, then cancel the old one, so there is never a gap."""
        old = self.stop_order_id
        self.place_stop(side, qty, new_stop)
        if old:
            try:
                self.ex.cancel_order(old, self.symbol)
            except Exception:
                pass

    def flatten(self) -> None:
        """Kill switch: cancel everything and close any open position at market."""
        self.ex.cancel_all_orders(self.symbol)
        for pos in self.ex.fetch_positions([self.symbol]):
            qty = abs(float(pos.get("contracts") or 0))
            if qty:
                side = "sell" if pos["side"] == "long" else "buy"
                self.ex.create_order(self.symbol, "market", side, qty, params={"reduceOnly": True})
