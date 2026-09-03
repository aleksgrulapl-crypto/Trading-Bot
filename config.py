# config.py
# ============================
# CONFIG MODULE (FINAL VERSION — CLEAN + UNIFIED)
# ============================

import os

# Base API
API_BASE = os.getenv("API_BASE", "https://api-capital.backend-capital.com")

# Auth / session endpoints
API_LOGIN = f"{API_BASE}/api/v1/session"
API_REFRESH = None

# Account / positions / market endpoints
API_ACCOUNTS = f"{API_BASE}/api/v1/accounts"
API_ACCOUNT = f"{API_BASE}/api/v1/accounts"
API_POSITIONS = f"{API_BASE}/api/v1/positions"
API_MARKET = f"{API_BASE}/api/v1/markets"
API_HISTORY_TRANSACTIONS = f"{API_BASE}/api/v1/history/transactions"

# Credentials (must be provided via environment in production)
CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY")
CAPITAL_USERNAME = os.getenv("CAPITAL_USERNAME")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD")

# Debugging / logging control
DEBUG_LOGS = os.getenv("DEBUG_LOGS", "False").lower() in ("1", "true", "yes")

# Trading parameters
MAX_POSITIONS_PER_TICKER = int(os.getenv("MAX_POSITIONS_PER_TICKER", 3))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", 0.50))
EQUITY_PERCENT = float(os.getenv("EQUITY_PERCENT", 0.50))
LEVERAGE = int(os.getenv("LEVERAGE", 5))

# Hard caps applied on top of EQUITY_PERCENT so a single trade never risks more
# than a fixed amount of capital, regardless of account balance:
#   MAX_EQUITY_PER_TRADE   – equity (before leverage) allocated to a trade
#   MAX_EXPOSURE_PER_TRADE – leveraged exposure allocated to a trade
# Defaults: up to £200 equity per trade, £1000 exposure at 5x leverage.
MAX_EQUITY_PER_TRADE = float(os.getenv("MAX_EQUITY_PER_TRADE", 200))
MAX_EXPOSURE_PER_TRADE = float(os.getenv("MAX_EXPOSURE_PER_TRADE", 1000))

# SL/TP expressed as a percentage price move from entry. With MAX_EXPOSURE_PER_TRADE
# capped at £1000, FIXED_SL_PERC=0.20 (20%) caps the loss at £200 (the £200
# equity used) and FIXED_TP_PERC=0.40 (40%) caps the gain at £400 (200% of equity).
FIXED_SL_PERC = float(os.getenv("FIXED_SL_PERC", 0.20))
FIXED_TP_PERC = float(os.getenv("FIXED_TP_PERC", 0.40))

# FX conversion (USD -> GBP)
# - Keep a default so the app works without env set.
# - Override in production via environment variable FX_USD_GBP.
try:
    FX_USD_GBP = float(os.getenv("FX_USD_GBP", "0.78"))
except Exception:
    FX_USD_GBP = 0.78

# Ticker-specific settings (can be extended via env or external config)
TICKER_SETTINGS = {
    "NVDA": {"min_size": 0.1},
    "TSLA": {"min_size": 0.1},
    "AMD":  {"min_size": 0.1},
    "AAPL": {"min_size": 0.1},
    "MSFT": {"min_size": 0.1},
    "PLTR": {"min_size": 0.1},
    "META": {"min_size": 0.1},
    "UNH": {"min_size": 0.1},
    "MU": {"min_size": 0.1},
    "PLUG": {"min_size": 0.1},
    "NFLX": {"min_size": 0.1},
    "AMAT": {"min_size": 0.1},
    "WMT": {"min_size": 0.1},
    "GOOGL": {"min_size": 0.1},
    "AMZN": {"min_size": 0.1},
    "CRM": {"min_size": 0.1},
    "INTC": {"min_size": 0.1},
    "BABA": {"min_size": 0.1},
    "SHOP": {"min_size": 0.1},
    "COIN": {"min_size": 0.1}
}

# UI / dashboard
DASHBOARD_TITLE = os.getenv("DASHBOARD_TITLE", "AG Capital Trader")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "Killen123%")

# Timezone and reporting
TIMEZONE = os.getenv("TIMEZONE", "Europe/London")

# Trade time lock: block opening new trades during a high-volatility window
# (e.g. the US cash market open at 14:30 UK time), to reduce volatility
# exposure and improve winrate. Hours are in 24h UK local time (TIMEZONE).
TRADE_LOCK_ENABLED = os.getenv("TRADE_LOCK_ENABLED", "True").lower() in ("1", "true", "yes")
TRADE_LOCK_START_HOUR = int(os.getenv("TRADE_LOCK_START_HOUR", 14))
TRADE_LOCK_END_HOUR = int(os.getenv("TRADE_LOCK_END_HOUR", 15))
DAILY_REPORT_ENABLED = os.getenv("DAILY_REPORT_ENABLED", "True").lower() in ("1", "true", "yes")
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", 22))
DAILY_REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", 0))

# File paths and persistence
TRADE_LOG_PATH = os.getenv("TRADE_LOG_PATH", os.getenv("TRADE_LOG_FILE", "/data/trade_log.json"))
DAILY_REPORT_FILE = os.getenv("DAILY_REPORT_FILE", "/tmp/daily_report.json")

# Cache and timing
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 2))

# EPIC mapping (can be extended)
EPIC_MAP = {
    "NVDA": "NVDA",
    "MU": "MU",
    "MSFT": "MSFT",
    "PLTR": "PLTR",
    "QBTS": "QBTS",
    "AAPL": "AAPL",
    "AMD": "AMD",
    "META": "META",
    "INTC": "INTC",
    "TSLA": "TSLA",
    "AMZN": "AMZN",
    "SPCX": "SPCX",
    "NFLX": "NFLX",
    "AVGO": "AVGO",
    "GOOG": "GOOG",
    "WDC": "WDC",
    "MRVL": "MRVL",
    "STX": "STX",
    "AMAT": "AMAT",
    "ORCL": "ORCL",
    "UNH": "UNH",
    "NBIS": "NBIS",
    "LRCX": "LRCX",
    "ISRG": "ISRG",
    "BE": "BE",
    "LITE": "LITE",
    "LLY": "LLY",
    "WMT": "WMT",
    "CSCO": "CSCO",
    "PLUG": "PLUG",
    "GOLD": "GOLD"
}

# Trailing stop defaults
TRAIL_ACTIVATION_PERC = float(os.getenv("TRAIL_ACTIVATION_PERC", 0.005))
TRAIL_SL_PERC = float(os.getenv("TRAIL_SL_PERC", 0.60))
