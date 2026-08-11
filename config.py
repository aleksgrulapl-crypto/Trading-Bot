# ============================
# CONFIG MODULE (FINAL VERSION)
# ============================

import os

# ---------------------------------------------------------
# API BASE
# ---------------------------------------------------------

API_BASE = "https://api-capital.backend-capital.com"

# ---------------------------------------------------------
# AUTH & SESSION
# ---------------------------------------------------------

API_LOGIN = f"{API_BASE}/api/v1/session"

# IMPORTANT: CFD accounts DO NOT support refresh-token
API_REFRESH = None   # Disabled intentionally

# ---------------------------------------------------------
# ACCOUNT & BALANCE
# ---------------------------------------------------------

API_ACCOUNTS = f"{API_BASE}/api/v1/accounts"
API_ACCOUNT = f"{API_BASE}/api/v1/accounts"   # Required for dashboard/account fetch

# ---------------------------------------------------------
# POSITIONS & MARKET DATA
# ---------------------------------------------------------

API_POSITIONS = f"{API_BASE}/api/v1/positions"

# EPIC / instrument lookup endpoint (used by verify_epic)
API_MARKET = f"{API_BASE}/api/v1/markets"

# ---------------------------------------------------------
# USER CREDENTIALS (ENV VARIABLES)
# ---------------------------------------------------------

CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY")
CAPITAL_USERNAME = os.getenv("CAPITAL_USERNAME")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD")

# ---------------------------------------------------------
# RISK SETTINGS (UPDATED FOR NEW ENGINE)
# ---------------------------------------------------------

# Max number of open positions per ticker
MAX_POSITIONS_PER_TICKER = 3

# % of available balance allocated per trade (20% recommended)
EQUITY_PERCENT = 0.20

# Leverage multiplier (Capital.com default for US stocks is 5:1)
LEVERAGE = 5

# Legacy fixed SL/TP percentages (unused in new engine but kept for compatibility)
FIXED_SL_PERC = 0.05
FIXED_TP_PERC = 0.10

# ---------------------------------------------------------
# TICKER SETTINGS (MINIMUM SIZE PER INSTRUMENT)
# ---------------------------------------------------------

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

# ---------------------------------------------------------
# DASHBOARD SETTINGS
# ---------------------------------------------------------

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
