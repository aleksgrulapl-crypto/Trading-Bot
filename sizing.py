# sizing.py
# ============================
# SIZING MODULE (FINAL — SYMBOL-AWARE + SAFE SL/TP)
# ============================

import logging
from typing import Optional, Dict, Any

import session
import config

logger = logging.getLogger("sizing")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [sizing] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        return float(v)
    except Exception:
        return None


def _normalize_direction(direction: Optional[str]) -> Optional[str]:
    if not direction:
        return None
    d = str(direction).strip().lower()
    if d in ("buy", "b", "long"):
        return "buy"
    if d in ("sell", "s", "short"):
        return "sell"
    return None


def calculate_size(entry_price, sl_price, tp_price, direction, symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Calculate position size using:
      - a fraction of AVAILABLE equity (config.EQUITY_PERCENT)
      - leverage multiplier (config.LEVERAGE)
      - per-ticker minimum size enforcement (config.TICKER_SETTINGS)
      - SL/TP safety validation

    Returns:
      {"blocked": True, "reason": "..."} on failure
      {"blocked": False, "size": float} on success
    """

    # 1) Normalize and validate numeric inputs
    entry = _safe_float(entry_price)
    sl = _safe_float(sl_price)
    tp = _safe_float(tp_price)
    dir_norm = _normalize_direction(direction)

    if entry is None or entry <= 0:
        return {"blocked": True, "reason": "invalid_entry_price"}

    if dir_norm not in ("buy", "sell"):
        return {"blocked": True, "reason": "invalid_direction"}

    if sl is None or tp is None:
        return {"blocked": True, "reason": "missing_sl_or_tp"}

    # 2) Validate SL/TP relative to entry depending on direction
    if dir_norm == "buy":
        # For buys: SL < entry < TP
        if not (sl < entry < tp):
            return {"blocked": True, "reason": "invalid_sl_tp_buy"}
        if sl == entry or tp == entry:
            return {"blocked": True, "reason": "sl_tp_equal_entry"}
    else:  # sell
        # For sells: TP < entry < SL
        if not (tp < entry < sl):
            return {"blocked": True, "reason": "invalid_sl_tp_sell"}
        if sl == entry or tp == entry:
            return {"blocked": True, "reason": "sl_tp_equal_entry"}

    # 3) Fetch account available margin
    try:
        account_raw = session.get_account()
        account = session.enrich_account(account_raw) if account_raw is not None else {}
    except Exception:
        logger.exception("Failed to fetch account for sizing")
        return {"blocked": True, "reason": "account_fetch_failed"}

    available = account.get("available", 0) or 0
    try:
        available = float(available)
    except Exception:
        available = 0.0

    if available <= 0:
        return {"blocked": True, "reason": "no_available_margin"}

    # 4) Determine equity to use and exposure, capped by MAX_EQUITY_PER_TRADE /
    #    MAX_EXPOSURE_PER_TRADE so a single trade never risks more than a fixed
    #    amount of capital regardless of account balance.
    equity_to_use = available * float(getattr(config, "EQUITY_PERCENT", 0.5))
    max_equity_per_trade = float(getattr(config, "MAX_EQUITY_PER_TRADE", 0) or 0)
    if max_equity_per_trade > 0:
        equity_to_use = min(equity_to_use, max_equity_per_trade)

    leverage = float(getattr(config, "LEVERAGE", 1))
    exposure = equity_to_use * leverage
    max_exposure_per_trade = float(getattr(config, "MAX_EXPOSURE_PER_TRADE", 0) or 0)
    if max_exposure_per_trade > 0:
        exposure = min(exposure, max_exposure_per_trade)

    # 5) Convert exposure to raw size (units)
    try:
        raw_size = exposure / entry
    except Exception:
        return {"blocked": True, "reason": "division_error"}

    # Round to 2 decimals (adjust as needed for instrument granularity)
    size = round(raw_size, 2)

    # 6) Enforce per-ticker minimum size
    ticker_key = None
    if symbol:
        try:
            ticker_key = str(symbol).upper()
        except Exception:
            ticker_key = None
    else:
        # fallback to last symbol in shared_state if present
        ticker_key = session.shared_state.get("last_symbol") if session.shared_state else None
        if ticker_key:
            ticker_key = str(ticker_key).upper()

    min_size = 0.1  # default minimum
    try:
        if ticker_key:
            ticker_settings = getattr(config, "TICKER_SETTINGS", {}).get(ticker_key, {})
            min_size = float(ticker_settings.get("min_size", min_size))
    except Exception:
        min_size = 0.1

    if size < min_size:
        size = float(min_size)

    # 7) Final safety checks
    if size <= 0:
        return {"blocked": True, "reason": "computed_size_nonpositive"}

    # 8) Return final sizing
    return {
        "blocked": False,
        "size": float(round(size, 2)),
        "exposure": float(round(exposure, 2)),
        "equity_used": float(round(equity_to_use, 2)),
        "min_size": float(min_size)
    }
