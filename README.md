# perps-bot

Automated long/short trading for crypto perpetual futures, built risk-first for a small (~$500) account.

**Exchange:** Kraken derivatives (ccxt id `krakenfutures`, symbol `BTC/USD:USD`). Kraken runs a free demo
environment at demo-futures.kraken.com; `LiveBroker(sandbox=True)` points there.

**Status:** backtest and paper trading work. Live trading is deliberately disabled until the Kraken adapter is tested on the exchange's sandbox, and paper results have been reviewed.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
python -m pytest -q                       # risk-control tests
python run.py backtest                    # synthetic data demo
python run.py fetch --since 2025-01-01    # real candles from the exchange's public API
python run.py backtest --csv data/<file>.csv
python run.py paper                       # simulated fills on live prices, no API keys needed
python run.py kill                        # emergency stop
```

## Risk controls (bot/risk.py)
Every order goes through `RiskManager.approve()`; nothing reaches a broker otherwise.
- **Position sizing by risk:** size = (equity x risk %) / stop distance, so a stopped-out trade loses a fixed amount.
- **Leverage cap:** configurable, with a hard ceiling of 5x in code that config can't override.
- **Mandatory stop-loss** on the correct side of entry; live orders place it on the exchange as reduce-only so it survives a crash.
- **Liquidation buffer:** rejects any trade whose stop sits too close to the estimated liquidation price.
- **Daily loss limit:** no new trades for the rest of the UTC day once hit.
- **Max drawdown kill switch:** goes flat and stops permanently until a human re-arms it.
- **Manual kill switch:** `python run.py kill` (or create a file named `KILL`).

## API keys
Keys are read only from environment variables (see `.env.example`). Use trade-only keys with withdrawals disabled.

## Honest caveats
- The included EMA/ATR strategy is a placeholder to exercise the plumbing, not a proven edge.
- Fees, funding and slippage are modeled approximately. Backtests overstate real results.
- With leverage, a $500 account can lose most of its value quickly. Only fund what you can lose entirely.
