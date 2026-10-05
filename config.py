# config.py
# ============================
# CONFIG MODULE (FINAL VERSION — CLEAN + UNIFIED)
# ============================

import os
from typing import List, Tuple

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
# MAX_POSITIONS_PER_TICKER is an overall safety-net cap on concurrently open
# trades for one ticker across all timeframes (3, matching the 3 timeframes
# the strategy runs). MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME is the primary
# rule: at most 1 open trade per ticker per individual timeframe, so the
# same timeframe can't open a second trade while one is already running.
MAX_POSITIONS_PER_TICKER = int(os.getenv("MAX_POSITIONS_PER_TICKER", 3))
MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME = int(os.getenv("MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME", 1))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", 0.50))
EQUITY_PERCENT = float(os.getenv("EQUITY_PERCENT", 0.50))
LEVERAGE = int(os.getenv("LEVERAGE", 5))

# Hard caps applied on top of EQUITY_PERCENT:
#   MAX_EQUITY_PER_TRADE    – equity (before leverage) allocated to one trade
#   MAX_EQUITY_PER_TICKER   – combined equity allowed across all open trades for one ticker
#   MAX_EXPOSURE_PER_TRADE  – leveraged exposure allocated to one trade
# Defaults: up to £125 equity per trade (£625 exposure at 5x leverage), and
# up to £375 combined equity per ticker across its 3 timeframes (3 x £125).
MAX_EQUITY_PER_TRADE = float(os.getenv("MAX_EQUITY_PER_TRADE", 125))
MAX_EQUITY_PER_TICKER = float(os.getenv("MAX_EQUITY_PER_TICKER", 375))
MAX_EXPOSURE_PER_TRADE = float(os.getenv("MAX_EXPOSURE_PER_TRADE", 625))

# SL/TP expressed as a percentage of the EQUITY USED for the trade (not the
# leveraged exposure/full account balance), so risk is predictable regardless
# of leverage. E.g. with MAX_EQUITY_PER_TRADE=£250: FIXED_SL_PERC=0.10 (10%)
# caps the loss at £25 (10% of the £250 equity used) and FIXED_TP_PERC=0.40
# (40%) caps the gain at £100 (40% of the £250 equity used). sl_tp.FixedSLTP
# converts these equity-based percentages into the actual price-move
# percentage by dividing by LEVERAGE (since exposure = equity_used *
# LEVERAGE, a price move of equity_perc/LEVERAGE yields exactly
# equity_perc * equity_used in £ terms).
FIXED_SL_PERC = float(os.getenv("FIXED_SL_PERC", 0.10))
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
# Dual login roles: "Owner" has full control (close/delete/edit trades); "Viewer"
# can only view the Dashboard/Analytics pages. DASHBOARD_OWNER_PASSWORD falls back
# to the original single DASHBOARD_PASSWORD so existing deployments keep working.
DASHBOARD_OWNER_PASSWORD = os.getenv("DASHBOARD_OWNER_PASSWORD", DASHBOARD_PASSWORD)
DASHBOARD_VIEWER_PASSWORD = os.getenv("DASHBOARD_VIEWER_PASSWORD", "Viewer123$")
# "Investor" has the same read-only access as Viewer, plus the ROI page
# (per-investor gain/loss breakdown) so investors can check their own returns.
DASHBOARD_INVESTOR_PASSWORD = os.getenv("DASHBOARD_INVESTOR_PASSWORD", "Investor123$")

# Timezone and reporting
TIMEZONE = os.getenv("TIMEZONE", "Europe/London")

# Trade time lock: block opening new trades during a high-volatility window
# (e.g. the US cash market open at 14:30 UK time), to reduce volatility
# exposure and improve winrate. Hours are in 24h UK local time (TIMEZONE).
#
# TEMPORARY (2-month trial, started Oct 2026): the general daily lock windows
# have been switched OFF while bot behaviour is monitored. Only the Monday
# 08:30-09:30 UK window remains active, to avoid weekend-reopen spikes/drops.
# This is not a removal of the feature — to restore the previous behaviour,
# set TRADE_LOCK_WINDOWS back to "08:00-09:30,13:00-15:00" (applies every day)
# or set TRADE_LOCK_ENABLED=False to disable the lock entirely.
TRADE_LOCK_ENABLED = os.getenv("TRADE_LOCK_ENABLED", "True").lower() in ("1", "true", "yes")
TRADE_LOCK_START_HOUR = int(os.getenv("TRADE_LOCK_START_HOUR", 14))
TRADE_LOCK_END_HOUR = int(os.getenv("TRADE_LOCK_END_HOUR", 15))
# Each window is "HH:MM-HH:MM" (applies every day) or "Day:HH:MM-HH:MM" (applies
# only on that day of the week, e.g. "Mon:08:30-09:30"). Comma-separated.
TRADE_LOCK_WINDOWS_RAW = os.getenv("TRADE_LOCK_WINDOWS", "Mon:08:30-09:30")

_WEEKDAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_trade_lock_windows(raw: str) -> List[Tuple[int, int, int]]:
    """Parse TRADE_LOCK_WINDOWS into (weekday_or_-1, start_minute, end_minute)
    tuples. weekday_or_-1 is -1 (meaning "every day") when no day prefix is
    given, otherwise 0=Monday .. 6=Sunday, matching datetime.weekday()."""
    windows: List[Tuple[int, int, int]] = []
    if not raw:
        return windows
    for segment in str(raw).split(","):
        chunk = segment.strip()
        if not chunk or "-" not in chunk:
            continue

        weekday = -1
        time_part = chunk
        if ":" in chunk:
            maybe_day, _, rest = chunk.partition(":")
            if maybe_day.strip().lower() in _WEEKDAY_NAMES:
                weekday = _WEEKDAY_NAMES[maybe_day.strip().lower()]
                time_part = rest.strip()

        if "-" not in time_part:
            continue
        start_raw, end_raw = time_part.split("-", 1)

        def _to_minutes(part: str) -> int:
            text = str(part).strip()
            if ":" in text:
                hh, mm = text.split(":", 1)
                return (int(hh) * 60) + int(mm)
            return int(text) * 60

        try:
            start_m = _to_minutes(start_raw)
            end_m = _to_minutes(end_raw)
        except Exception:
            continue

        start_m = max(0, min(24 * 60, start_m))
        end_m = max(0, min(24 * 60, end_m))
        if start_m == end_m:
            continue
        windows.append((weekday, start_m, end_m))
    return windows


TRADE_LOCK_WINDOWS = _parse_trade_lock_windows(TRADE_LOCK_WINDOWS_RAW)

# Guard against auto-closing a trade moments after it was opened: a
# freshly-created broker position can transiently fail to appear in the
# /positions list (or 404 from the single-position endpoint) for a few
# seconds while the broker propagates it. Disappearance-based close
# detection (history_sync.sync_closed_trades) ignores trades younger than
# this many seconds; a genuine size==0 report is still honoured immediately.
AUTOCLOSE_GRACE_PERIOD_SECONDS = float(os.getenv("AUTOCLOSE_GRACE_PERIOD_SECONDS", 60))

# Hedging: when enabled, an incoming signal opposite to an already-open
# position for the same ticker (e.g. a "buy" alert while a short is open) is
# treated as a legitimate hedge and allowed through, instead of being
# suppressed by the duplicate-open-position guard. A same-direction signal
# for a ticker that already has an open position of that direction is still
# suppressed as a duplicate.
HEDGING_ENABLED = os.getenv("HEDGING_ENABLED", "True").lower() in ("1", "true", "yes")
DAILY_REPORT_ENABLED = os.getenv("DAILY_REPORT_ENABLED", "True").lower() in ("1", "true", "yes")
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", 22))
DAILY_REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", 0))

# File paths and persistence
TRADE_LOG_PATH = os.getenv("TRADE_LOG_PATH", os.getenv("TRADE_LOG_FILE", "/data/trade_log.json"))
DAILY_REPORT_FILE = os.getenv("DAILY_REPORT_FILE", "/tmp/daily_report.json")

# Deposit/withdrawal bookkeeping log (Owner-only dashboard page). This is a
# manual ledger for tracking money moved in/out of the broker account for
# accurate equity context; it does not move real funds.
DEPOSITS_LOG_PATH = os.getenv("DEPOSITS_LOG_PATH", "/data/deposits_log.json")

# ROI allocation: the named investor receives a flat ownership-share
# override (e.g. for covering backend/hosting costs as the account owner),
# taken off the top before the remaining share pool is split by time-weighted
# capital contribution. Set OWNER_INVESTOR_OVERRIDE_PCT to 0 to disable.
OWNER_INVESTOR_NAME = os.getenv("OWNER_INVESTOR_NAME", "Aleks")
OWNER_INVESTOR_OVERRIDE_PCT = float(os.getenv("OWNER_INVESTOR_OVERRIDE_PCT", 15))

# Per-investor ROI tier store (Owner-only, changeable from the Investors
# page). By default every non-owner investor is "tradingview" tier: they
# only share in PnL from TradingView/Hedge (automated) trades, never from
# "Trader" (manual/discretionary) trades. The Owner can upgrade an investor
# to "full" tier so they additionally share pro-rata in Trader-trade PnL
# (no performance fee applies to that sleeve — see deposits.investor_breakdown).
INVESTOR_TIERS_PATH = os.getenv("INVESTOR_TIERS_PATH", "/data/investor_tiers.json")

# "Become an Investor" pledge log (visible to any logged-in user on the
# Investors page). Logged-in users can record a pledge (name + amount) of
# funds they intend to pay in via bank transfer; the Owner then confirms it
# once payment is actually received, which creates a matching entry in the
# deposits ledger. No real money moves through the app itself.
INVESTOR_PLEDGES_PATH = os.getenv("INVESTOR_PLEDGES_PATH", "/data/investor_pledges.json")

# Payment details shown on the "Become an Investor" card so people know
# where to send funds. These should be your Capital.com trading account's
# own deposit details (Capital.com > Deposit > Bank transfer) so funds land
# directly in the trading account and the balance updates automatically via
# the dashboard's existing polling -- rather than a personal account that
# would need a manual top-up afterwards. Leave blank (the default) to hide
# a given field; set these via environment variables rather than committing
# real account/reference details to source control.
INVESTOR_BANK_BENEFICIARY = os.getenv("INVESTOR_BANK_BENEFICIARY", "")
INVESTOR_BANK_PAYMENT_REFERENCE = os.getenv("INVESTOR_BANK_PAYMENT_REFERENCE", "")
INVESTOR_BANK_ACCOUNT_NUMBER = os.getenv("INVESTOR_BANK_ACCOUNT_NUMBER", "")
INVESTOR_BANK_SORT_CODE = os.getenv("INVESTOR_BANK_SORT_CODE", "")
INVESTOR_BANK_NAME_ADDRESS = os.getenv("INVESTOR_BANK_NAME_ADDRESS", "")
INVESTOR_BANK_PAYMENT_NOTE = os.getenv(
    "INVESTOR_BANK_PAYMENT_NOTE",
    "You must include the payment reference above exactly as shown, or "
    "Capital.com cannot credit the deposit to this trading account. "
    "Please also submit the pledge below so it can be matched to you once "
    "the balance updates.",
)
INVESTOR_CRYPTO_BTC_ADDRESS = os.getenv("INVESTOR_CRYPTO_BTC_ADDRESS", "")
INVESTOR_CRYPTO_ETH_ADDRESS = os.getenv("INVESTOR_CRYPTO_ETH_ADDRESS", "")

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
TRAIL_ACTIVATION_PERC = float(os.getenv("TRAIL_ACTIVATION_PERC", 0.02))
TRAIL_ACTIVATION_TP_FRACTION = float(os.getenv("TRAIL_ACTIVATION_TP_FRACTION", 0.25))
TRAIL_SL_PERC = float(os.getenv("TRAIL_SL_PERC", 0.50))
