"""Central config — reads from .env, exposes tunables."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Market ---
# SYMBOLS = comma-separated list. SYMBOL kept for backwards compat / single-symbol use.
SYMBOL = os.getenv("SYMBOL", "NQ=F")           # NASDAQ 100 E-mini Futures on Yahoo
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", f"{SYMBOL},QQQ").split(",") if s.strip()]
FVG_TIMEFRAME = "5m"
BIAS_TIMEFRAME = "1h"
BIAS_EMA_PERIOD = 50

# --- Strategy (mirrors Pine script defaults) ---
DISPLACEMENT_THRESHOLD = 0.7                  # body / range
RISK_RR = 2.0                                 # target = 2R
ATR_PERIOD = 14
ATR_MULT = 0.5                                # stop = swing ± 0.5 * ATR
SWING_LEFT = 5
SWING_RIGHT = 5

# NY session (Eastern Time)
SESSION_START = "09:30"
SESSION_END = "16:00"
SESSION_TZ = "America/New_York"

# --- Bayesian self-teaching ---
MIN_POSTERIOR_TO_ALERT = float(os.getenv("MIN_POSTERIOR_TO_ALERT", "0.45"))
USE_LCB = True                                # Use lower credible bound (more conservative)
CREDIBLE_ALPHA = 0.10                         # 90% lower bound
SIGNAL_TIMEOUT_HOURS = 48                     # close still-open signals after this

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Storage ---
DB_PATH = os.getenv("DB_PATH", "trades.db")
BAYES_MODEL_PATH = os.getenv("BAYES_MODEL_PATH", "bayes_model.json")

# --- Loop ---
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# --- Daily summary ---
# When (ET) to push the daily P&L recap to Telegram. 16:15 = 15 min after NYSE close.
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "16:15")

# --- Paper trading ---
# When True, log every fired signal as an open paper position, push an OPEN
# notification, then push a CLOSE notification (with P&L in ticks) when it
# resolves. The bot does NOT place real orders — paper trading is the only mode.
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() in ("1", "true", "yes")

# Tick size per symbol (price units per tick).
# NQ/ES futures: 0.25.  ETFs/indices: 0.01.
TICK_SIZES = {
    "NQ=F": 0.25,
    "MNQ=F": 0.25,
    "ES=F": 0.25,
    "MES=F": 0.25,
    "RTY=F": 0.10,
    "YM=F": 1.0,
    "QQQ": 0.01,
    "SPY": 0.01,
    "IWM": 0.01,
    "^NDX": 0.01,
    "^GSPC": 0.01,
}
DEFAULT_TICK_SIZE = 0.01

def tick_size(symbol: str) -> float:
    return TICK_SIZES.get(symbol, DEFAULT_TICK_SIZE)

# --- Self-learning / adaptive threshold ---
# When True, the bot nudges its per-symbol confidence threshold up after a
# losing streak and down after a winning streak, within these bounds.
ADAPTIVE_THRESHOLD = os.getenv("ADAPTIVE_THRESHOLD", "true").lower() in ("1", "true", "yes")
ADAPTIVE_MIN = float(os.getenv("ADAPTIVE_MIN", "0.40"))
ADAPTIVE_MAX = float(os.getenv("ADAPTIVE_MAX", "0.75"))
ADAPTIVE_WINDOW = int(os.getenv("ADAPTIVE_WINDOW", "20"))   # last N closed trades
ADAPTIVE_STEP = float(os.getenv("ADAPTIVE_STEP", "0.02"))   # nudge size per cycle
