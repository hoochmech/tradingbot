#!/usr/bin/env python3
"""Command line entry point.

  python run.py backtest [--csv data/BTC.csv]   backtest on a CSV (or synthetic data if omitted)
  python run.py fetch --since 2025-01-01        download public candles to data/
  python run.py paper                           paper trade on live public prices (no keys needed)
  python run.py kill                            create the kill switch file; the bot stops opening trades
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd

from bot.backtest import TF_HOURS, load_csv, run_backtest, synthetic_ohlcv
from bot.broker import PaperBroker
from bot.config import load_config
from bot.risk import RiskManager, RiskRejected
from bot.strategy import add_indicators, signal

log = logging.getLogger("bot")


def cmd_backtest(cfg, args):
    df = load_csv(args.csv) if args.csv else synthetic_ohlcv()
    if not args.csv:
        print("No --csv given: using SYNTHETIC random-walk data. Results say nothing about real markets.\n")
    res = run_backtest(df, cfg)
    print(json.dumps(res.summary(), indent=2))


def _exchange(cfg):
    import ccxt

    return getattr(ccxt, cfg.exchange)({"enableRateLimit": True})


def cmd_fetch(cfg, args):
    ex = _exchange(cfg)
    since = ex.parse8601(f"{args.since}T00:00:00Z")
    rows = []
    while True:
        batch = ex.fetch_ohlcv(cfg.symbol, cfg.timeframe, since=since, limit=300)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 1
        if len(batch) < 2:
            break
    out = Path("data") / f"{cfg.symbol.replace('/', '-').replace(':', '_')}_{cfg.timeframe}.csv"
    out.parent.mkdir(exist_ok=True)
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).drop_duplicates(
        "timestamp"
    ).to_csv(out, index=False)
    print(f"wrote {len(rows)} candles to {out}")


def cmd_paper(cfg, args):
    ex = _exchange(cfg)
    risk = RiskManager(cfg.risk, cfg.kill_switch_file)
    broker = PaperBroker(cfg.starting_equity, cfg.taker_fee_pct, cfg.funding_rate_per_8h_pct,
                         TF_HOURS.get(cfg.timeframe, 1))
    last_ts = None
    log.info("paper trading %s %s on %s with $%.2f", cfg.symbol, cfg.timeframe, cfg.exchange, cfg.starting_equity)
    while True:
        raw = ex.fetch_ohlcv(cfg.symbol, cfg.timeframe, limit=200)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").iloc[:-1]  # drop the still-forming candle
        ts = df.index[-1]
        if ts != last_ts:
            last_ts = ts
            bar = df.iloc[-1]
            if t := broker.on_bar(bar.high, bar.low, bar.close, ts):
                log.info("closed %s %s pnl=%.2f", t.side, t.reason, t.pnl)
            equity = broker.equity(bar.close)
            risk.on_equity(equity)
            if risk.kill_switch_active:
                if broker.position:
                    broker.close(bar.close, "kill_switch", ts)
                log.warning("KILLED: %s. Flat and stopped.", risk.killed_reason)
                return
            intent = signal(add_indicators(df, cfg.strategy), cfg.strategy)
            if intent:
                if broker.position and broker.position.side != intent.side:
                    broker.close(bar.close, "reverse", ts)
                if not broker.position:
                    try:
                        o = risk.approve(intent, equity, 0)
                        broker.open(o, ts)
                        log.info("open %s qty=%.6f @%.2f stop=%.2f lev=%.2fx liq~%.2f",
                                 o.side, o.qty, o.entry, o.stop, o.leverage, o.est_liq_price)
                    except RiskRejected as e:
                        log.info("rejected: %s", e)
            log.info("bar %s close=%.2f equity=%.2f", ts, bar.close, broker.equity(bar.close))
        time.sleep(args.poll)


def cmd_kill(cfg, args):
    Path(cfg.kill_switch_file).touch()
    print(f"Kill switch set ({cfg.kill_switch_file}). Delete the file to re-arm after you review.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest"); b.add_argument("--csv")
    f = sub.add_parser("fetch"); f.add_argument("--since", default="2025-01-01")
    pp = sub.add_parser("paper"); pp.add_argument("--poll", type=int, default=30)
    sub.add_parser("kill")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if cfg.live:
        raise SystemExit("Live trading isn't enabled yet. Run backtests and paper trading first.")
    {"backtest": cmd_backtest, "fetch": cmd_fetch, "paper": cmd_paper, "kill": cmd_kill}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
