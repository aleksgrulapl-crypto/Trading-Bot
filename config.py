# ============================
# CONFIG MODULE (FINAL VERSION)
# ============================

import os

API_BASE = "https://api-capital.backend-capital.com"

# --- AUTH & SESSION ---
API_LOGIN = f"{API_BASE}/api/v1/session"

# IMPORTANT: CFD accounts DO NOT support refresh-token
API_REFRESH = None   # Removed / disabled

# --- ACCOUNT & BALANCE ---
API_ACCOUNTS = f"{API_BASE}/api/v1/accounts"
API_ACCOUNT = f"{API_BASE}/api/v1/accounts"   # <-- REQUIRED

# --- POSITIONS & MARKET DATA ---
API_POSITIONS = f"{API_BASE}/api/v1/positions"

# EPIC / instrument lookup endpoint (used by verify_epic)
API_MARKET = f"{API_BASE}/api/v1/markets"

# --- USER CREDENTIALS ---
CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY")
CAPITAL_USERNAME = os.getenv("CAPITAL_USERNAME")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD")

# --- RISK SETTINGS ---
MAX_POSITIONS_PER_TICKER = 3
EQUITY_PERCENT = 0.50
FIXED_SL_PERC = 0.05
FIXED_TP_PERC = 0.10

TICKER_SETTINGS = {
    "NVDA": {"min_size": 0.1},
    "TSLA": {"min_size": 0.1},
    "AMD":  {"min_size": 0.1},
    "AAPL": {"min_size": 0.1},
    "MSFT": {"min_size": 0.1},
    "PLTR": {"min_size": 0.1},
    "META": {"min_size": 0.1},
    "SMCI": {"min_size": 0.1},
    "QBTS": {"min_size": 0.1},
    "IONQ": {"min_size": 0.1},
    "RKLB": {"min_size": 0.1},
    "ASTS": {"min_size": 0.1},
    "SOUN": {"min_size": 0.1},
    "OPEN": {"min_size": 0.1},
    "PATH": {"min_size": 0.1},
    "JOBY": {"min_size": 0.1},
    "PLUG": {"min_size": 0.1},
}

# --- DASHBOARD SETTINGS ---
DASHBOARD_TITLE = "AG Capital Trader"
DASHBOARD_PASSWORD = "Killen123%"
TIMEZONE = "Europe/London"
DAILY_REPORT_ENABLED = True
DAILY_REPORT_HOUR = 22
DAILY_REPORT_MINUTE = 0
TRADE_LOG_FILE = "/tmp/trade_log.json"
DAILY_REPORT_FILE = "/tmp/daily_report.json"
CACHE_TTL_SECONDS = 10

# ---------------------------------------------------------
# EPIC MAPPING (Correct for YOUR Capital.com account)
# ---------------------------------------------------------
# Your logs prove your account uses RAW tickers as EPICs:
# "epic": "MU"
# "epic": "PLTR"
# "epic": "NVDA"
# NOT "US.MU", "US.PLTR", "US.NVDA"

EPIC_MAP = {
    "NVDA": "NVDA",
    "MU": "MU",
    "TSLA": "TSLA",
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "AMD": "AMD",
    "META": "META",
    "GOOGL": "GOOGL",
    "AMZN": "AMZN",
    "PLTR": "PLTR"
}
