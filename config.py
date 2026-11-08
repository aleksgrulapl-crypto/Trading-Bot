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

# The /debug/* and /raw* routes (which can place orders / close positions) are
# disabled (404) unless DEBUG_ROUTES is truthy.
DEBUG_ROUTES = os.getenv("DEBUG_ROUTES", "False").lower() in ("1", "true", "yes")

# Shared secret required on every POST /webhook. Send it in the alert JSON as
# "secret", as ?secret=... on the webhook URL, or in an X-Webhook-Secret
# header. Empty = no check (a warning is logged at startup).
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# Dashboard login expires after this many minutes of inactivity.
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))

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
# Defaults: up to £150 equity per trade/timeframe (£750 exposure at 5x
# leverage), and up to £450 combined equity per ticker across its 3
# timeframes. These are the base values: a stock with >= GATE_MIN_TRADES
# closed trades is scaled by PERF_UPSIZE_FACTOR (net-positive, positive
# Sharpe → £180 / £540) or PERF_DOWNSIZE_FACTOR (net-negative → £75 / £225).
MAX_EQUITY_PER_TRADE = float(os.getenv("MAX_EQUITY_PER_TRADE", 150))
MAX_EQUITY_PER_TICKER = float(os.getenv("MAX_EQUITY_PER_TICKER", 450))
MAX_EXPOSURE_PER_TRADE = float(os.getenv("MAX_EXPOSURE_PER_TRADE", 750))
PERF_UPSIZE_FACTOR = float(os.getenv("PERF_UPSIZE_FACTOR", 1.2))
PERF_DOWNSIZE_FACTOR = float(os.getenv("PERF_DOWNSIZE_FACTOR", 0.5))

# Max units per trade by share price in GBP: (upper price bound, max units).
# Above the last bound only the equity-based calculation applies. Each band
# is roughly the £750 exposure ceiling, so cheap stocks can't pile up units.
PRICE_BAND_MAX_UNITS = (
    (10.0, 75.0),
    (50.0, 15.0),
    (100.0, 7.5),
    (250.0, 3.0),
    (500.0, 1.5),
)

# Weekly-return gating (see gating.py). Stocks are ranked by Sharpe ratio
# (and net PnL) from closed TradingView/Hedge trades; the bottom
# GATE_BOTTOM_FRACTION (plus any net-negative stock) is "weak". Weak stocks'
# new entries are then:
#   - rejected when the week's return is below GATE_LOSS_PCT (negative week),
#   - rejected when it is between GATE_REJECT_PCT and GATE_REDUCE_PCT,
#   - sized down by GATE_REDUCE_SCALE when it is at/above GATE_REDUCE_PCT.
# Stocks with fewer than GATE_MIN_TRADES closed trades are never gated.
WEEKLY_GATE_ENABLED = os.getenv("WEEKLY_GATE_ENABLED", "True").lower() in ("1", "true", "yes")
GATE_BOTTOM_FRACTION = float(os.getenv("GATE_BOTTOM_FRACTION", 0.20))
GATE_MIN_TRADES = int(os.getenv("GATE_MIN_TRADES", 5))
GATE_LOSS_PCT = float(os.getenv("GATE_LOSS_PCT", 0.0))
GATE_REJECT_PCT = float(os.getenv("GATE_REJECT_PCT", 5.0))
GATE_REDUCE_PCT = float(os.getenv("GATE_REDUCE_PCT", 10.0))
GATE_REDUCE_SCALE = float(os.getenv("GATE_REDUCE_SCALE", 0.5))
REJECTED_TRADES_PATH = os.getenv(
    "REJECTED_TRADES_PATH",
    os.path.join(os.path.dirname(os.getenv("TRADE_LOG_PATH", os.getenv("TRADE_LOG_FILE", "/data/trade_log.json"))) or ".", "rejected_trades.json"),
)

# Per-ticker equity scaling by win rate: sizing.calculate_size() scales the
# equity caps above down for tickers with a weaker TradingView/Hedge win
# rate (from the Analytics "TradingView" tab), so a consistently losing
# ticker is allocated less equity than a consistently winning one, rather
# than both risking the same size. The scale factor is clamp(win_rate, MIN,
# 1.0) — e.g. a ticker with a 45% win rate gets 45% of the ceiling above. A
# ticker with no decided (win/loss) TradingView/Hedge trades yet (i.e. not
# yet present in the Analytics tab) is exempt and gets the full ceiling.
# TICKER_EQUITY_SCALE_MIN is a floor so a losing ticker is scaled down, not
# starved to £0 (which would permanently lock it out of ever recovering).
TICKER_EQUITY_SCALE_MIN = float(os.getenv("TICKER_EQUITY_SCALE_MIN", 0.3))

# SL cap: the strategy now uses the TradingView alert's own SL/TP levels
# directly (see webhook.py) instead of overriding them with a fixed
# risk-percentage calculation, since the strategy's signals depend on those
# levels. The TP is used as-is, uncapped. The SL, however, is capped so the
# loss it implies never exceeds MAX_SL_PERC_OF_EQUITY of the equity used for
# the trade (not the full leveraged exposure) — an overly wide alert SL is
# pulled in to this cap; a tighter SL passes through unchanged. sl_tp.
# FixedSLTP.cap_sl_to_equity_risk() converts this equity-based percentage
# into the actual price-move percentage by dividing by LEVERAGE (since
# exposure = equity_used * LEVERAGE, a price move of equity_perc/LEVERAGE
# yields exactly equity_perc * equity_used in £ terms).
MAX_SL_PERC_OF_EQUITY = float(os.getenv("MAX_SL_PERC_OF_EQUITY", 0.20))

# Fixed SL/TP are kept only as a defensive fallback for the rare alert that
# doesn't supply its own sl/tp levels (see webhook.py), expressed as a
# percentage of the EQUITY USED for the trade (not the leveraged
# exposure/full account balance), so risk is predictable regardless of
# leverage. E.g. with MAX_EQUITY_PER_TRADE=£250: FIXED_SL_PERC=0.10 (10%)
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
DASHBOARD_TITLE = os.getenv("DASHBOARD_TITLE", "AGC Trader")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
# The Owner logs in as "owner" with DASHBOARD_OWNER_PASSWORD (falls back to the
# legacy DASHBOARD_PASSWORD). There is no built-in default: if neither is set,
# Owner login is disabled. Viewer/Investor access comes from registered accounts
# approved by the Owner (see users.py), not from shared passwords.
DASHBOARD_OWNER_PASSWORD = os.getenv("DASHBOARD_OWNER_PASSWORD", DASHBOARD_PASSWORD)
USERS_PATH = os.getenv("USERS_PATH", "/data/users.json")
# Public site URL used in emailed links (password reset), e.g. https://your-app.onrender.com
# Set this so links never depend on the request's Host header.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
# Cached, percentages-only performance figures for the public landing page.
PUBLIC_SNAPSHOT_PATH = os.getenv("PUBLIC_SNAPSHOT_PATH", "/data/public_snapshot.json")

# Timezone and reporting
TIMEZONE = os.getenv("TIMEZONE", "Europe/London")

# Trade time lock: block opening new trades during a high-volatility window
# (e.g. the US cash market open at 14:30 UK time), to reduce volatility
# exposure and improve winrate. Hours are in 24h UK local time (TIMEZONE).
# TEMPORARY: the trade time lock is switched OFF by default while the bot is
# monitored (no windows block trading). To restore it, set
# TRADE_LOCK_ENABLED=True (the windows below, e.g. "Mon:08:30-09:30,14:00-15:00",
# then apply); nothing else needs to change.
TRADE_LOCK_ENABLED = os.getenv("TRADE_LOCK_ENABLED", "False").lower() in ("1", "true", "yes")
TRADE_LOCK_START_HOUR = int(os.getenv("TRADE_LOCK_START_HOUR", 14))
TRADE_LOCK_END_HOUR = int(os.getenv("TRADE_LOCK_END_HOUR", 15))
# Each window is "HH:MM-HH:MM" (applies every day) or "Day:HH:MM-HH:MM" (applies
# only on that day of the week, e.g. "Mon:08:30-09:30"). Comma-separated.
TRADE_LOCK_WINDOWS_RAW = os.getenv("TRADE_LOCK_WINDOWS", "Mon:08:30-09:30,14:00-15:00")

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
# Weekday-only daily PnL email to NOTIFY_EMAIL_TO (UK time).
DAILY_PNL_EMAIL_HOUR = int(os.getenv("DAILY_PNL_EMAIL_HOUR", 21))
DAILY_PNL_EMAIL_MINUTE = int(os.getenv("DAILY_PNL_EMAIL_MINUTE", 0))

# Email notifications: sent directly to NOTIFY_EMAIL_TO via SMTP when a
# position is closed (always) and opened (only when NOTIFY_EMAIL_ENABLED / the
# dashboard "Receive Notifications" toggle is On).
NOTIFY_EMAIL_ENABLED = os.getenv("NOTIFY_EMAIL_ENABLED", "False").lower() in ("1", "true", "yes")
# Comma-separated list of recipient addresses.
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", "")
# "From" address shown on the email; defaults to the SMTP login username.
NOTIFY_EMAIL_FROM = os.getenv("NOTIFY_EMAIL_FROM", "")
# SMTP server used to actually send the email (e.g. smtp.gmail.com / 587 with
# STARTTLS, or your provider's equivalent). Use an app password, not your
# normal account password, where supported.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "True").lower() in ("1", "true", "yes")
# Persisted override for the dashboard's Owner-only "send to email" toggle,
# so turning it on/off (and the recipient address) survives restarts without
# needing to edit environment variables. Falls back to NOTIFY_EMAIL_ENABLED /
# NOTIFY_EMAIL_TO above when this file doesn't exist yet.
NOTIFY_SETTINGS_PATH = os.getenv("NOTIFY_SETTINGS_PATH", "/data/notify_settings.json")
# Stores the hash/date used to auto-stamp "last updated" on the Terms of Service.
TRAIL_SETTINGS_PATH = os.getenv("TRAIL_SETTINGS_PATH", "/data/trail_settings.json")
# Owner master switch for accepting new trades (see trading_switch.py).
TRADING_SWITCH_PATH = os.getenv("TRADING_SWITCH_PATH", "/data/trading_switch.json")
UI_PREFS_PATH = os.getenv("UI_PREFS_PATH", "/data/ui_prefs.json")
TERMS_VERSION_PATH = os.getenv("TERMS_VERSION_PATH", "/data/terms_version.json")

# Events & sentiment for open trades (events.py). Free Finnhub key from
# https://finnhub.io; with no key the feature stays dormant.
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
EVENTS_CACHE_PATH = os.getenv("EVENTS_CACHE_PATH", "/data/events_cache.json")
# Earnings alert lead time (days) and how often news/earnings are re-fetched.
EVENTS_ALERT_DAYS = int(os.getenv("EVENTS_ALERT_DAYS", 2))
EVENTS_NEWS_TTL_MIN = int(os.getenv("EVENTS_NEWS_TTL_MIN", 30))
EVENTS_EARNINGS_TTL_HOURS = int(os.getenv("EVENTS_EARNINGS_TTL_HOURS", 6))

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

# Pledge amount bounds (GBP), enforced both server-side and in the
# "Become an Investor" form. Keeps individual pledges within a manageable
# range while the feature is new; raise/remove later as needed.
PLEDGE_MIN_AMOUNT = float(os.getenv("PLEDGE_MIN_AMOUNT", 50.0))
PLEDGE_MAX_AMOUNT = float(os.getenv("PLEDGE_MAX_AMOUNT", 500.0))

# Investor withdrawal requests. A withdrawal is requested, then approved by
# the Owner. Once approved, a matching drop in the live trading balance is
# auto-detected and recorded. If no balance change shows up within
# WITHDRAWAL_AUTO_SETTLE_DAYS (the Owner paid the investor personally), the
# investor's units are transferred to the Owner at the current NAV instead
# (0 disables the automatic fallback; the Owner can still settle manually).
WITHDRAWAL_REQUESTS_PATH = os.getenv("WITHDRAWAL_REQUESTS_PATH", "/data/withdrawal_requests.json")
WITHDRAWAL_AUTO_SETTLE_DAYS = float(os.getenv("WITHDRAWAL_AUTO_SETTLE_DAYS", 3))
# Deposited capital is locked for this many days before it can be requested
# back (applies to self-service requests, not to requests the Owner records).
INVESTOR_LOCK_DAYS = int(os.getenv("INVESTOR_LOCK_DAYS", 30))

# Automatic balance-change detection: on each dashboard refresh, the live
# broker balance is compared against what trading PnL alone would explain
# since the last check. An unexplained jump (no corresponding closed
# trades/fees) is treated as money moved in/out of the account outside the
# app and is recorded automatically in the Deposits ledger. If a pending
# pledge's amount matches the detected change, that pledge is auto-confirmed
# and attributed to its investor instead of logged as "Unassigned".
BALANCE_STATE_PATH = os.getenv("BALANCE_STATE_PATH", "/data/balance_state.json")
# Calendar-aligned balance snapshots (one entry per UK calendar day, the
# first balance observed that day) used as the opening-balance basis for
# the Dashboard's Daily/Weekly/Monthly Return metrics - these are
# period-locked (e.g. Daily = 00:00-23:59 UK), not rolling windows.
BALANCE_HISTORY_PATH = os.getenv("BALANCE_HISTORY_PATH", "/data/balance_history.json")
# Base unexplained balance delta (GBP, either direction) below which it's
# ignored entirely as FX/rounding noise. The real deposit/withdrawal floors
# below are higher still, to keep the Deposits ledger free of clutter.
BALANCE_AUTO_DETECT_TOLERANCE = float(os.getenv("BALANCE_AUTO_DETECT_TOLERANCE", 1.0))
# Minimum unexplained balance *increase* (GBP) before it's recorded as a
# deposit. Uses the same floor as PLEDGE_MIN_AMOUNT below, since a real
# deposit is expected to be at least a minimum pledge's worth; anything
# smaller is treated as drift and left to accumulate.
# Minimum unexplained balance *decrease* (GBP) before it's recorded as a
# withdrawal. Filters out small everyday noise (overnight/swap fees, spread
# drift, etc.) that isn't a real withdrawal and would otherwise clutter the
# Deposits ledger.
BALANCE_AUTO_DETECT_WITHDRAWAL_MIN = float(os.getenv("BALANCE_AUTO_DETECT_WITHDRAWAL_MIN", 100.0))
# Maximum GBP difference between a detected change and a pending pledge's
# amount for them to be considered a match.
PLEDGE_MATCH_TOLERANCE = float(os.getenv("PLEDGE_MATCH_TOLERANCE", 2.0))
# Minimum seconds between balance-change checks, to avoid redundant file
# I/O when the dashboard is polled frequently.
BALANCE_AUTO_DETECT_MIN_INTERVAL_SECONDS = float(os.getenv("BALANCE_AUTO_DETECT_MIN_INTERVAL_SECONDS", 15))

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
TRAIL_ACTIVATION_PERC = float(os.getenv("TRAIL_ACTIVATION_PERC", 0.01))
TRAIL_ACTIVATION_TP_FRACTION = float(os.getenv("TRAIL_ACTIVATION_TP_FRACTION", 0.5))
# Absolute GBP unrealized-PnL floor: trailing activates as soon as EITHER
# this amount of profit (converted to GBP) OR the percentage-based
# activation threshold above is reached, whichever comes first. This lets
# small/low-priced positions activate trailing promptly instead of waiting
# for a large percentage price move.
TRAIL_ACTIVATION_PNL_GBP = float(os.getenv("TRAIL_ACTIVATION_PNL_GBP", 15.0))
# Fraction of unrealized profit locked in by the SL as soon as trailing
# activates (e.g. 0.70 -> SL sits at entry + 70% of the profit made so far).
TRAIL_SL_PERC = float(os.getenv("TRAIL_SL_PERC", 0.70))
# As profit extends further beyond the activation threshold, the locked-in
# fraction ramps up by TRAIL_TIGHTEN_STEP_PERC per "activation multiple"
# past 1x, up to the TRAIL_MAX_PERC ceiling — so the SL hugs price more
# closely the deeper a trade goes in-profit.
TRAIL_TIGHTEN_STEP_PERC = float(os.getenv("TRAIL_TIGHTEN_STEP_PERC", 0.20))
TRAIL_MAX_PERC = float(os.getenv("TRAIL_MAX_PERC", 0.95))
# Throttling so the trail doesn't spam Capital.com with SL modifications.
TRAIL_MIN_STEP_PERC = float(os.getenv("TRAIL_MIN_STEP_PERC", 0.001))
TRAIL_MIN_UPDATE_INTERVAL_SECONDS = float(os.getenv("TRAIL_MIN_UPDATE_INTERVAL_SECONDS", 60))
TRAIL_FAILURE_COOLDOWN_SECONDS = float(os.getenv("TRAIL_FAILURE_COOLDOWN_SECONDS", 120))
# TP trail (all trades): once a trade is TP_TRAIL_TRIGGER_FRACTION of the way
# to its TP, the SL jumps to TP_TRAIL_LOCK_FRACTION of the profit at that
# moment, the TP is removed, and the SL keeps ratcheting at that fraction of
# the profit for as long as the trade keeps running.
TP_TRAIL_TRIGGER_FRACTION = float(os.getenv("TP_TRAIL_TRIGGER_FRACTION", 0.90))
TP_TRAIL_LOCK_FRACTION = float(os.getenv("TP_TRAIL_LOCK_FRACTION", 0.80))

# Shared broker-positions cache / back-off (reduces timeouts under load).
POSITIONS_CACHE_TTL_SECONDS = float(os.getenv("POSITIONS_CACHE_TTL_SECONDS", 3))
POSITIONS_MAX_BACKOFF_SECONDS = float(os.getenv("POSITIONS_MAX_BACKOFF_SECONDS", 30))
POSITIONS_STALE_MAX_AGE_SECONDS = float(os.getenv("POSITIONS_STALE_MAX_AGE_SECONDS", 120))
# Seconds a trade may be confirmed gone at the broker with no history
# evidence before it is force-closed in the log.
CLOSE_EVIDENCE_TIMEOUT_SECONDS = float(os.getenv("CLOSE_EVIDENCE_TIMEOUT_SECONDS", 300))
