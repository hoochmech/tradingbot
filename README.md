# tradingbot

Automated long/short trading for crypto perpetual futures, built risk-first for a small (~$500) account.

**Status:** backtesting and paper trading work, with phone alerts. Live trading is deliberately disabled
until Kraken API access for your account is confirmed, orders are tested on Kraken's demo exchange, and
paper results have been reviewed.

## How it trades: 1:2 safe vs aggressive
The account is split into two "sleeves", each running its own strategy on its own coin:

| Sleeve | Share | Coin | Strategy | Max leverage | Risk per trade |
|---|---|---|---|---|---|
| safe | 1/3 (~$167) | BTC | Breakout of the last 40 four-hour candles, only in the direction of the 200 EMA; wide 3 ATR stop, 3:1 target | 2x | 0.75% (~$1.25) |
| aggressive | 2/3 (~$333) | ETH | Same breakout idea; 2.5 ATR stop, 2:1 target | 4x | 1.5% (~$5) |

Runs on 4-hour candles. Every trade has a stop-loss and a profit target 2-3x as far away as the stop.

**Backtest (Kraken data, Jan 2025 to Nov 2026, fees and funding included):** settings were picked on 2025 data
and checked on 2026. 2025: +13.2%, worst drawdown 5.8%. 2026: +7.6%, worst drawdown 5.8%. Whole period: +25.1%,
132 trades, kill switch never tripped. The settings next to these also made money in both periods, which is a better
sign than a single lucky setting. Past results do not predict future ones.
Both strategies are reasonable starting points, **not proven money-makers**. Backtest on real data first.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
cp .env.example .env
python -m pytest -q                       # risk-control tests
python run.py fetch --since 2025-01-01    # real candles from Kraken's public API
python run.py backtest                    # backtest both sleeves on that data
python run.py paper                       # simulated trades on live prices, no API keys needed
python run.py kill                        # emergency stop
```
The bot has to run on a computer that stays on (your PC, or a ~$5/month cloud server).

## Monitoring from your phone (Telegram)
1. Install Telegram on your Android phone.
2. Message **@BotFather**, send `/newbot`, and follow the prompts. Copy the token it gives you into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the `"chat":{"id":...}` number into `TELEGRAM_CHAT_ID`.
4. Run `python run.py test-alert`. You should get a message.

While the bot runs you get a message for every trade opened or closed, a status update every 6 hours, and
an alert if it stops. You can text the bot:
- `/status` for equity and open positions
- `/kill` to close everything and stop the bot

Only your chat id is obeyed; messages from anyone else are ignored.

## Risk controls (bot/risk.py, bot/portfolio.py)
Every order goes through the risk manager; nothing reaches a broker otherwise.
- **Position sizing by risk:** size = (equity x risk %) / stop distance, so a stopped-out trade loses a fixed amount.
- **Leverage cap per sleeve**, with a hard ceiling of 5x in code that config can't override.
- **Mandatory stop-loss** on the correct side of entry; live orders place it on the exchange as reduce-only so it survives a crash.
- **Liquidation buffer:** rejects any trade whose stop sits too close to the estimated liquidation price.
- **Daily loss limit (whole account):** no new trades for the rest of the UTC day once hit.
- **Max drawdown kill switch (whole account):** closes everything and stops until a human re-arms it.
- **Manual kill switch:** `/kill` from your phone, `python run.py kill`, or create a file named `KILL`.

## Kraken setup
US perpetual futures on Kraken are offered through Kraken Derivatives US (NinjaTrader Clearing, a CFTC-registered FCM)
inside Kraken Pro, for eligible, verified US clients.
1. Fully verify your Kraken account.
2. In Kraken Pro, open the derivatives / futures section and complete the derivatives application (experience and risk questions, disclosures).
3. Once approved, fund the derivatives account (start small).
4. Create an API key with **trade permissions only, withdrawals disabled**. Check that the key covers derivatives.

**Open question:** the bot's live adapter uses Kraken's futures API (ccxt `krakenfutures`). Kraken hasn't
clearly documented whether US derivatives accounts trade through that API. Confirm before going live.

## Honest caveats
- Fees, funding and slippage are modeled approximately. Backtests overstate real results.
- Results on synthetic data (used when no CSVs exist) are meaningless.
- With leverage, a $500 account can lose most of its value quickly. Only fund what you can lose entirely.
