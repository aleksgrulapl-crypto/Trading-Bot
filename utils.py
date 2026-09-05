# utils.py
# ============================
# UTILS MODULE (FINAL + CLEAN + SAFE)
# ============================

import datetime
from decimal import Decimal, getcontext
from typing import Optional

# High precision for safe_float operations
getcontext().prec = 12

# Optional timezone support for UK-local trade timestamps.
try:
    import pytz  # type: ignore
    _UK_TZ = pytz.timezone("Europe/London")
except Exception:
    _UK_TZ = None


# ---------------------------------------------------------
# TIMESTAMP (CUSTOM FORMAT)
# ---------------------------------------------------------

def timestamp() -> str:
    """
    Returns a UTC timestamp in format:
    YYYY-MM-DD HH.MM.SS
    Example: 2026-08-17 18.49.00
    """
    now = datetime.datetime.utcnow()
    return now.strftime("%Y-%m-%d %H.%M.%S")


def uk_timestamp() -> str:
    """
    Returns the current time as an ISO-8601 string in UK local time
    (Europe/London, correctly accounting for GMT/BST), matching the format
    trade_log.py uses internally for time_entered/time_exited. Use this
    (instead of timestamp(), which is UTC) whenever recording a trade's
    entry/exit time, so every trade-log timestamp is consistently in UK time.
    """
    if _UK_TZ is not None:
        try:
            return datetime.datetime.now(_UK_TZ).isoformat()
        except Exception:
            pass
    return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------
# SAFE FLOAT + ROUNDING
# ---------------------------------------------------------

def safe_float(x) -> float:
    """
    Safely converts input to float using Decimal.
    Returns 0.0 on failure.
    """
    try:
        # Convert None to 0.0 explicitly
        if x is None:
            return 0.0
        # Decimal from string preserves precision for inputs like "1,234.56"
        if isinstance(x, str):
            # remove common thousands separators
            cleaned = x.replace(",", "").strip()
            return float(Decimal(cleaned))
        return float(Decimal(str(x)))
    except Exception:
        return 0.0


def round2(x) -> float:
    """
    Rounds a numeric value to 2 decimals safely.
    Returns 0.0 on failure.
    """
    try:
        return round(float(Decimal(str(x))), 2)
    except Exception:
        return 0.0


# ---------------------------------------------------------
# PROFIT/LOSS CALCULATION
# ---------------------------------------------------------

def calculate_profit_loss(direction, open_price, current_price, size) -> float:
    """
    Universal PnL calculator for BUY/SELL (or Long/Short).
    Returns a float (not None). Uses safe_float for conversions.
    """
    if not direction or open_price is None or current_price is None:
        return 0.0

    op = safe_float(open_price)
    cp = safe_float(current_price)
    sz = safe_float(size)

    # Normalize direction to handle "BUY", "buy", "Long", "long", etc.
    try:
        d = str(direction).strip().lower()
    except Exception:
        d = ""

    if d in ("buy", "long"):
        pnl = (cp - op) * sz
        return round2(pnl)

    if d in ("sell", "short"):
        pnl = (op - cp) * sz
        return round2(pnl)

    # Unknown direction
    return 0.0
