#!/usr/bin/env python3
"""Command line entry point.

  python run.py backtest [--data-dir data]   backtest every sleeve (synthetic data if CSVs are missing)
  python run.py fetch --since 2025-01-01     download public candles for every sleeve's symbol into data/
  python run.py paper                        paper trade on live public prices (no API keys needed)
  python run.py kill                         create the kill switch file: close everything and stop
  python run.py test-alert                   send a test message to your phone
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from bot.backtest import csv_name, load_csv, run_backtest, synthetic_data
from bot.config import load_config
from bot.notify import Notifier
from bot.portfolio import Portfolio

log = logging.getLogger("bot")


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


def _exchange(cfg):
    import ccxt

    return getattr(ccxt, cfg.exchange)({"enableRateLimit": True})


def _candles(ex, symbol, timeframe, limit=700) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").iloc[:-1]  # drop the still-forming candle


def cmd_backtest(cfg, args):
    files = {s.symbol: Path(args.data_dir) / csv_name(s.symbol, cfg.timeframe) for s in cfg.sleeves}
    if all(f.exists() for f in files.values()):
        data = {sym: load_csv(str(f)) for sym, f in files.items()}
    else:
        print("CSV data not found: using SYNTHETIC random-walk data. Results say nothing about real markets.")
        print("Run `python run.py fetch` first for real candles.\n")
        data = synthetic_data(cfg)
    print(json.dumps(run_backtest(data, cfg).summary(), indent=2))


def cmd_fetch(cfg, args):
    ex = _exchange(cfg)
    Path(args.data_dir).mkdir(exist_ok=True)
    for s in cfg.sleeves:
        since = ex.parse8601(f"{args.since}T00:00:00Z")
        rows = []
        while True:
            batch = ex.fetch_ohlcv(s.symbol, cfg.timeframe, since=since, limit=1000)
            if not batch or len(batch) < 2:
                break
            rows += batch
            since = batch[-1][0] + 1
        out = Path(args.data_dir) / csv_name(s.symbol, cfg.timeframe)
        pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).drop_duplicates(
            "timestamp").to_csv(out, index=False)
        print(f"wrote {len(rows)} candles to {out}")


def cmd_paper(cfg, args):
    ex = _exchange(cfg)
    notifier = Notifier()
    pf = Portfolio(cfg, cfg.kill_switch_file)
    notifier.listen(pf.status, cfg.kill_switch_file)
    notifier.send(f"Paper trading started on {cfg.exchange} with ${cfg.starting_equity:,.2f}.\n{pf.status()}")
    last_ts, next_beat = None, datetime.now(timezone.utc)
    while True:
        try:
            frames = {s.symbol: _candles(ex, s.symbol, cfg.timeframe) for s in cfg.sleeves}
        except Exception as e:
            log.warning("data fetch failed: %s", e)
            time.sleep(args.poll)
            continue
        ts = min(df.index[-1] for df in frames.values())
        if ts != last_ts or Path(cfg.kill_switch_file).exists():
            last_ts = ts
            for ev in pf.step(frames, ts):
                notifier.send(ev)
            if pf.guard.killed_reason:
                notifier.send(f"Bot STOPPED: {pf.guard.killed_reason}\n{pf.status()}")
                return
            if pf.guard.daily_loss_pct(pf.equity) >= cfg.risk.daily_loss_limit_pct:
                log.info("daily loss limit reached; no new trades until 00:00 UTC")
        if datetime.now(timezone.utc) >= next_beat:
            notifier.send(pf.status())
            next_beat = datetime.now(timezone.utc) + timedelta(hours=cfg.heartbeat_hours)
        time.sleep(args.poll)


def cmd_kill(cfg, args):
    Path(cfg.kill_switch_file).touch()
    print(f"Kill switch set ({cfg.kill_switch_file}). Delete the file to re-arm after you review.")


def cmd_test_alert(cfg, args):
    n = Notifier()
    if not n.enabled:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set in .env")
    n.send("Test alert from your trading bot. Phone alerts are working.")
    print("sent")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest"); b.add_argument("--data-dir", default="data")
    f = sub.add_parser("fetch"); f.add_argument("--since", default="2025-01-01"); f.add_argument("--data-dir", default="data")
    pp = sub.add_parser("paper"); pp.add_argument("--poll", type=int, default=30)
    sub.add_parser("kill")
    sub.add_parser("test-alert")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    cfg = load_config(args.config)
    if cfg.live:
        raise SystemExit("Live trading isn't enabled yet. Run backtests and paper trading first.")
    {"backtest": cmd_backtest, "fetch": cmd_fetch, "paper": cmd_paper, "kill": cmd_kill,
     "test-alert": cmd_test_alert}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
