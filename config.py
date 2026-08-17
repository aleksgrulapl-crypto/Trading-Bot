# ============================
# CONFIG MODULE (FINAL VERSION — CLEAN + UNIFIED)
# ============================

import os

API_BASE = "https://api-capital.backend-capital.com"

API_LOGIN = f"{API_BASE}/api/v1/session"
API_REFRESH = None

API_ACCOUNTS = f"{API_BASE}/api/v1/accounts"
API_ACCOUNT = f"{API_BASE}/api/v1/accounts"

API_POSITIONS = f"{API_BASE}/api/v1/positions"
API_MARKET = f"{API_BASE}/api/v1/markets"
API_HISTORY_TRANSACTIONS = "https://api-capital.backend-capital.com/api/v1/history/transactions"

CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY")
CAPITAL_USERNAME = os.getenv("CAPITAL_USERNAME")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD")

MAX_POSITIONS_PER_TICKER = 3
RISK_PER_TRADE = 0.50
EQUITY_PERCENT = 0.50
LEVERAGE = 5

FIXED_SL_PERC = 0.10
FIXED_TP_PERC = 0.20

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

DASHBOARD_TITLE = "AG Capital Trader"
DASHBOARD_PASSWORD = "Killen123%"
TIMEZONE = "Europe/London"

DAILY_REPORT_ENABLED = True
DAILY_REPORT_HOUR = 22
DAILY_REPORT_MINUTE = 0

TRADE_LOG_FILE = "/data/trade_log.json"
DAILY_REPORT_FILE = "/tmp/daily_report.json"

CACHE_TTL_SECONDS = 2

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

# Trailing stop settings (Option A)
TRAIL_ACTIVATION_PERC = 0.50   # activate at +50% profit
TRAIL_SL_PERC = 0.30           # move SL to 30% of profit (one-time)
