from datetime import datetime, timezone

import pytest

from bot.backtest import run_backtest, synthetic_data
from bot.config import HARD_MAX_LEVERAGE, Config, RiskConfig
from bot.risk import OrderIntent, RiskManager, RiskRejected


def rm(tmp_path, **kw):
    return RiskManager(RiskConfig(**kw), kill_switch_file=tmp_path / "KILL")


def test_leverage_cannot_exceed_hard_cap():
    assert RiskConfig(max_leverage=50).max_leverage == HARD_MAX_LEVERAGE


def test_sizing_risks_one_percent(tmp_path):
    r = rm(tmp_path, max_leverage=5, risk_per_trade_pct=1)
    o = r.approve(OrderIntent("long", 100.0, 98.0), equity=500, open_positions=0)
    assert o.qty * 2 == pytest.approx(5.0)  # $5 lost at the stop
    assert o.leverage <= 5


def test_leverage_cap_shrinks_position(tmp_path):
    r = rm(tmp_path, max_leverage=2, risk_per_trade_pct=2, min_liq_buffer_pct=100)
    o = r.approve(OrderIntent("short", 100.0, 100.5), equity=500, open_positions=0)
    assert o.notional == pytest.approx(1000)


def test_requires_stop_on_correct_side(tmp_path):
    r = rm(tmp_path)
    with pytest.raises(RiskRejected):
        r.approve(OrderIntent("long", 100.0, 101.0), 500, 0)
    with pytest.raises(RiskRejected):
        r.approve(OrderIntent("short", 100.0, 0), 500, 0)


def test_rejects_stop_near_liquidation(tmp_path):
    # Risk-based sizing normally keeps the stop far from liquidation; this check is a backstop.
    r = rm(tmp_path, max_leverage=5, risk_per_trade_pct=2, min_liq_buffer_pct=1)
    with pytest.raises(RiskRejected):
        r.approve(OrderIntent("long", 100.0, 97.0), 500, 0)


def test_daily_loss_limit_blocks_trading(tmp_path):
    r = rm(tmp_path, daily_loss_limit_pct=3)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r.on_equity(500, now)
    r.on_equity(484, now)
    ok, why = r.can_trade(484, 0)
    assert not ok and "daily" in why
    r.on_equity(484, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert r.can_trade(484, 0)[0]


def test_drawdown_trips_kill_switch(tmp_path):
    r = rm(tmp_path, max_drawdown_pct=15)
    r.on_equity(500)
    r.on_equity(420)
    assert r.kill_switch_active


def test_kill_file(tmp_path):
    r = rm(tmp_path)
    (tmp_path / "KILL").touch()
    assert not r.can_trade(500, 0)[0]


def test_backtest_runs_both_sleeves():
    cfg = Config()
    s = run_backtest(synthetic_data(cfg), cfg).summary()
    assert set(s["sleeves"]) == {"safe", "aggressive"}
    assert all(v["liquidations"] == 0 for v in s["sleeves"].values())
    assert s["sleeves"]["aggressive"]["trades"] > 0


def test_capital_split_is_one_to_two():
    from bot.portfolio import Portfolio
    pf = Portfolio(Config(), "__none__")
    cash = {s.cfg.name: s.broker.cash for s in pf.sleeves}
    assert cash["aggressive"] == pytest.approx(2 * cash["safe"])
    assert sum(cash.values()) == pytest.approx(500)


def test_sleeve_leverage_still_hard_capped():
    from bot.config import SleeveConfig
    cfg = Config()
    sc = SleeveConfig("x", 1, "X/USD:USD", "breakout", max_leverage=20, risk_per_trade_pct=9)
    r = cfg.sleeve_risk(sc)
    assert r.max_leverage == HARD_MAX_LEVERAGE and r.risk_per_trade_pct == 2.0
