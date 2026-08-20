#!/usr/bin/env python3
# display_helpers.py
# Small helpers for formatting timestamps and currency for UI or reports.

from datetime import datetime
from typing import Optional

def format_human(dt_str: Optional[str]) -> Optional[str]:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %H:%M:%S")
    except Exception:
        try:
            # fallback common format
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H.%M.%S")
            return dt.strftime("%d-%m-%Y %H:%M:%S")
        except Exception:
            return dt_str

def format_currency(amount: Optional[float], symbol: str = "£") -> Optional[str]:
    if amount is None:
        return None
    try:
        return f"{symbol}{amount:,.2f}"
    except Exception:
        return str(amount)
