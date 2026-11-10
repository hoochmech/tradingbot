import pandas as pd
import pytest

from bot.broker import PaperBroker
from bot.config import Config, RiskConfig
from bot.portfolio import Portfolio
from bot.risk import ApprovedOrder, OrderIntent, RiskManager


def order(side="long", entry=100.0, stop=95.0, target=None):
    return ApprovedOrder(side, 1.0, entry, stop, target, 1.0, entry, 1.0 if side == "long" else 1e9)


def test_limit_entry_fills_only_if_price_comes_back_and_pays_maker_fee():
    b = PaperBroker(cash=1000, taker_fee_pct=0.05, maker_fee_pct=0.02, funding_per_8h_pct=0)
    b.place_limit(order(), ttl_bars=1)
    b.on_bar(high=103, low=100.5, close=102, open_=101)   # never trades back to 100
    assert b.position is None and b.pending is None and b.missed_entries == 1

    b.place_limit(order(), ttl_bars=1)
    b.on_bar(high=101, low=99.5, close=100.5, open_=100.8)
    assert b.position is not None and b.position.entry == 100
    assert 1000 - b.cash == pytest.approx(100 * 0.02 / 100)


def test_trailing_stop_ratchets_and_locks_in_profit():
    b = PaperBroker(cash=1000, funding_per_8h_pct=0, slippage_pct=0)
    b.open(order(stop=95), trail_dist=5)
    b.on_bar(high=110, low=104, close=109)
    assert b.position.stop == pytest.approx(105)
    b.on_bar(high=108, low=106, close=107)                # lower high: stop must not move back
    assert b.position.stop == pytest.approx(105)
    t = b.on_bar(high=107, low=104, close=104)
    assert t.reason == "trailing_stop" and t.exit == pytest.approx(105)
    assert t.pnl > 0


def test_short_trailing_stop():
    b = PaperBroker(cash=1000, funding_per_8h_pct=0, slippage_pct=0)
    b.open(order("short", stop=105), trail_dist=5)
    b.on_bar(high=96, low=90, close=91)
    assert b.position.stop == pytest.approx(95)


def test_vol_scaling_shrinks_risk_in_wild_markets(tmp_path):
    rm = RiskManager(RiskConfig(max_leverage=5, risk_per_trade_pct=1), tmp_path / "K")
    calm = rm.approve(OrderIntent("long", 100, 98), 500, 0, risk_scale=1.25)
    wild = rm.approve(OrderIntent("long", 100, 98), 500, 0, risk_scale=0.5)
    assert calm.qty == pytest.approx(2.5 * wild.qty)
    capped = rm.approve(OrderIntent("long", 100, 98), 500, 0, risk_scale=10)
    assert capped.qty * 2 == pytest.approx(500 * 0.02)   # never above the 2% hard ceiling


def test_vol_scale_reads_current_vs_usual_volatility():
    pf = Portfolio(Config(), "__none__")
    n = 400
    calm = [100 + (i % 2) * 0.5 for i in range(n)]
    df = pd.DataFrame({"open": calm, "high": [c + 0.5 for c in calm], "low": [c - 0.5 for c in calm],
                       "close": calm})
    wild = df.copy()
    wild.loc[n - 30:, "high"] += 5
    wild.loc[n - 30:, "low"] -= 5
    assert pf.vol_scale(wild, 14) == pytest.approx(0.5)
    assert pf.vol_scale(df, 14) == pytest.approx(1.0, abs=0.05)
