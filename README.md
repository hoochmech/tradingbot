# tradingbot

Automated long/short trading for crypto perpetual futures, built risk-first for a small (~$500) account.

**Status:** backtesting and paper trading work, with phone alerts. Live trading is deliberately disabled
until Kraken API access for your account is confirmed, orders are tested on Kraken's demo exchange, and
paper results have been reviewed.

## How it trades: 1:2 safe vs aggressive, six coins
| Group | Share | Coins | Max leverage | Risk per trade | Stop |
|---|---|---|---|---|---|
| safe | 1/3 (~$167, split evenly) | BTC, ETH | 2x | 0.75% of the coin's slice | 3 ATR, then trails 6 ATR behind the best price |
| aggressive | 2/3 (~$333, split evenly) | SOL, XRP, POL, ARB | 4x | 1.5% of the coin's slice | 2.5 ATR, then trails 5 ATR behind |

- **Signal:** price closes beyond the high or low of the last 40 four-hour candles, only in the direction of the 200 EMA trend.
- **Entries are limit orders** at the signal price (maker fee 0.02% instead of 0.05%). If price doesn't come back to fill it within one candle, the order is cancelled and the trade is skipped.
- **Trailing stops:** the stop starts at a fixed distance, then follows the best price reached and never moves backwards, so big trends can run.
- **Volatility sizing:** each trade's risk is scaled between 0.5x and 1.25x depending on whether the coin is wilder or calmer than usual. The 2% hard ceiling still applies.
- Total money at risk if every coin hits its stop at once stays about the same as the two-coin version, because each coin gets a smaller slice.

Both are reasonable starting points, **not proven money-makers**. Backtest on real data first.

**Backtest (Kraken 4h data, Jan 2025 to Nov 2026, fees and funding included):** 2025: +14.7%, worst drawdown
6.6%. 2026: +14.8%, worst drawdown 5.8%. Whole period: +30.9% ($500 to $654), all six coins net positive, kill
switch never tripped. The SOL/XRP/POL/ARB settings were carried over from ETH rather than tuned on those coins,
and all 18 nearby variations (lookback 20/40, stop 2-3 ATR, trail 3-8 ATR) also made money in both years.
Past results do not predict future ones.

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
