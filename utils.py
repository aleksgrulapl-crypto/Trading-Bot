# ============================
# UTILS MODULE (FINAL + CLEAN + SAFE)
# ============================

import datetime
from decimal import Decimal, getcontext

# High precision for safe_float operations
getcontext().prec = 12


# ---------------------------------------------------------
# TIMESTAMP (CUSTOM FORMAT)
# ---------------------------------------------------------

def timestamp():
    """
    Returns a UTC timestamp in format:
    YYYY-MM-DD HH.MM.SS
    Example: 2026-08-17 18.49.00
    """
    now = datetime.datetime.utcnow()
    return now.strftime("%Y-%m-%d %H.%M.%S")


# ---------------------------------------------------------
# SAFE FLOAT + ROUNDING
# ---------------------------------------------------------

def safe_float(x):
    """
    Safely converts input to float using Decimal.
    Returns 0.0 on failure.
    """
    try:
        return float(Decimal(str(x)))
    except Exception:
        return 0.0


def round2(x):
    """
    Rounds a numeric value to 2 decimals safely.
    """
    try:
        return round(float(Decimal(str(x))), 2)
    except Exception:
        return 0.0


# ---------------------------------------------------------
# PROFIT/LOSS CALCULATION
# ---------------------------------------------------------

def calculate_profit_loss(direction, open_price, current_price, size):
    """
    Universal PnL calculator for BUY/SELL.
    """
    if not direction or open_price is None or current_price is None:
        return 0.0

    open_price = safe_float(open_price)
    current_price = safe_float(current_price)
    size = safe_float(size)

    if direction.upper() == "BUY":
        return (current_price - open_price) * size

    if direction.upper() == "SELL":
        return (open_price - current_price) * size

    return 0.0
