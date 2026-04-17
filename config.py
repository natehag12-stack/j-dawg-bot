"""Central config — reads from .env, exposes tunables."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Market ---
SYMBOL = os.getenv("SYMBOL", "NQ=F")         # NASDAQ 100 E-mini Futures on Yahoo
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
