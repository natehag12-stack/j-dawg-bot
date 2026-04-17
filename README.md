# J-Dawg Bot

Smart Money Concepts alert bot for NASDAQ 100 futures (/NQ).
Mirrors the "PB Theory 70% WR" Pine script strategy, plus a Bayesian self-teaching layer that adapts alert confidence based on live win/loss history.

## What it does

Every minute during NY cash session (9:30–16:00 ET):

1. Pulls fresh 5m + 1H candles from Yahoo Finance
2. Checks the last closed 5m bar for the four entry conditions:
   - **1H bias** — close above 50-period EMA (longs) or below (shorts)
   - **Liquidity sweep** — wick through prior-day H/L or a 5-bar swing high/low
   - **Displacement candle** — body > 70% of range
   - **NY session** — inside 9:30–16:00 ET window
3. If all four line up → computes entry / ATR-based stop / 2R target
4. Bayesian layer gates the alert: only sends if posterior confidence > threshold
5. Fires a Telegram alert with levels + confidence + setup reason
6. Logs the signal; later bars check if target or stop was hit and feed the outcome back into the Bayesian model

## Setup (Windows)

### 1. Install Python 3.11+
Download from https://python.org if you don't have it. Check "Add Python to PATH" during install.

### 2. Create your Telegram bot
1. Open Telegram, search `@BotFather`, send `/newbot`
2. Pick a name and username ending in `bot`
3. Copy the **token** BotFather gives you (looks like `7842…:AAE…`)
4. Search `@userinfobot`, press Start — it replies with your numeric chat ID
5. Send any message to your new bot (this "activates" the chat)

### 3. Configure
Copy `.env.example` to `.env` and paste your token + chat ID:
```
TELEGRAM_TOKEN=7842...:AAE...
TELEGRAM_CHAT_ID=123456789
```

### 4. Run
Double-click `run.bat`.

First run auto-creates a virtual environment and installs dependencies. Subsequent runs start immediately.

Leave the window open while you want alerts. `Ctrl+C` to stop.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Live polling loop |
| `config.py` | All tunables (thresholds, EMA period, RR, etc.) |
| `data.py` | Yahoo Finance fetcher |
| `indicators.py` | FVG, PDH/PDL, swings, displacement, session, bias |
| `signals.py` | Combines indicators into long/short conditions |
| `bayesian.py` | Beta-Bernoulli self-teaching layer |
| `tracker.py` | SQLite signal log + outcome resolver |
| `telegram_bot.py` | Alert formatter |
| `trades.db` | Auto-created signal history |
| `bayes_model.json` | Auto-created model state (persists across restarts) |

## How the self-teaching works

The Bayesian layer models each side's win rate as a Beta distribution:

- **Start:** `Beta(α=1, β=1)` — uniform (zero info)
- **Each closed trade** updates the posterior:
  - Win → `α += 1`
  - Loss → `β += 1`
- **Posterior mean** = `α / (α + β)` — current best estimate of win rate
- **Lower credible bound** (default gate) = 10th percentile of posterior — the bot stays conservative until it has evidence

So after 0 trades, confidence is ~0.5 and alerts fire freely.
After 10 losses in a row, confidence drops to ~0.08 and the bot goes silent on that side until the edge returns.
After 20 wins and 5 losses, confidence climbs to ~0.70 and the bot is high-conviction.

The model state lives in `bayes_model.json` so it persists across bot restarts.

## Tuning

Edit `config.py` or `.env`:

- `MIN_POSTERIOR_TO_ALERT` — raise to 0.55 for fewer, higher-conviction alerts
- `BIAS_EMA_PERIOD` — try 21 or 200 for different regime filters
- `RISK_RR` — 2.0 default, try 1.5 for more frequent wins (but smaller R per win)
- `SYMBOL` — `NQ=F` (default), `QQQ` (ETF), `^NDX` (cash index)

## Disclaimer

Alerts only — this bot never places trades, just tells you when conditions align. Signals are based on historical patterns and carry no guarantee of future performance.
