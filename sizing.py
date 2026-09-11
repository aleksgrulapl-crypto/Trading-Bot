# sizing.py
# ============================
# SIZING MODULE (FINAL — SYMBOL-AWARE + SAFE SL/TP)
# ============================

import logging
import math
from typing import Optional, Dict, Any

import session
import config
from trade_log import load_raw_log

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


def _normalize_ticker(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(value).strip().upper() or None
    except Exception:
        return None


def _open_ticker_usage(ticker: Optional[str]) -> Dict[str, float]:
    ticker_norm = _normalize_ticker(ticker)
    if not ticker_norm:
        return {"open_count": 0, "equity_used": 0.0}

    leverage = float(getattr(config, "LEVERAGE", 1) or 1)
    if leverage <= 0:
        leverage = 1.0

    open_count = 0
    equity_used = 0.0
    try:
        trades = load_raw_log()
    except Exception:
        logger.exception("Failed to load trade log for ticker sizing")
        return {"open_count": 0, "equity_used": 0.0}

    for trade in trades or []:
        status = str(trade.get("status") or ("CLOSED" if trade.get("time_exited") else "OPEN")).strip().upper()
        if status != "OPEN":
            continue
        trade_ticker = _normalize_ticker(trade.get("ticker") or trade.get("epic"))
        if trade_ticker != ticker_norm:
            continue
        open_count += 1
        try:
            size = float(trade.get("size") or 0)
            entry = float(trade.get("entry_price") or 0)
            if size > 0 and entry > 0:
                equity_used += (size * entry) / leverage
        except Exception:
            continue

    return {
        "open_count": open_count,
        "equity_used": float(round(equity_used, 2)),
    }


def calculate_size(entry_price, sl_price, tp_price, direction, symbol: Optional[str] = None,
                   ticker: Optional[str] = None) -> Dict[str, Any]:
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

    # 4) Determine ticker-level capacity first. A ticker can have at most
    #    MAX_POSITIONS_PER_TICKER open trades, and their combined equity usage
    #    is capped by MAX_EQUITY_PER_TRADE.
    ticker_key = _normalize_ticker(ticker or symbol)
    ticker_usage = _open_ticker_usage(ticker_key)
    max_positions_per_ticker = int(getattr(config, "MAX_POSITIONS_PER_TICKER", 0) or 0)
    if max_positions_per_ticker > 0 and ticker_usage["open_count"] >= max_positions_per_ticker:
        return {
            "blocked": True,
            "reason": "max_positions_per_ticker_reached",
            "open_positions": int(ticker_usage["open_count"]),
            "ticker": ticker_key,
        }

    max_equity_per_trade = float(getattr(config, "MAX_EQUITY_PER_TRADE", 0) or 0)
    remaining_ticker_equity = None
    if max_equity_per_trade > 0:
        remaining_ticker_equity = max(0.0, max_equity_per_trade - ticker_usage["equity_used"])
        if remaining_ticker_equity <= 0:
            return {
                "blocked": True,
                "reason": "max_ticker_equity_reached",
                "equity_used_by_ticker": float(round(ticker_usage["equity_used"], 2)),
                "ticker": ticker_key,
            }

    # 5) Determine equity to use and exposure, capped by MAX_EQUITY_PER_TRADE /
    #    MAX_EXPOSURE_PER_TRADE so a ticker never risks more than the remaining
    #    allowed capital regardless of account balance.
    equity_to_use = available * float(getattr(config, "EQUITY_PERCENT", 0.5))
    if max_equity_per_trade > 0:
        equity_to_use = min(equity_to_use, max_equity_per_trade)
    if remaining_ticker_equity is not None:
        equity_to_use = min(equity_to_use, remaining_ticker_equity)

    leverage = float(getattr(config, "LEVERAGE", 1))
    exposure = equity_to_use * leverage
    max_exposure_per_trade = float(getattr(config, "MAX_EXPOSURE_PER_TRADE", 0) or 0)
    if max_exposure_per_trade > 0:
        exposure = min(exposure, max_exposure_per_trade)

    if equity_to_use <= 0 or exposure <= 0:
        return {"blocked": True, "reason": "ticker_capacity_exhausted", "ticker": ticker_key}

    # 6) Convert exposure to raw size (units)
    try:
        raw_size = exposure / entry
    except Exception:
        return {"blocked": True, "reason": "division_error"}

    # Round to 2 decimals (adjust as needed for instrument granularity)
    size = round(raw_size, 2)
    unclamped_size = size

    # 7) Enforce per-ticker minimum size
    min_size_key = _normalize_ticker(symbol) or ticker_key
    if symbol:
        try:
            min_size_key = str(symbol).upper()
        except Exception:
            min_size_key = None
    elif not min_size_key:
        # fallback to last symbol in shared_state if present
        min_size_key = session.shared_state.get("last_symbol") if session.shared_state else None
        if min_size_key:
            min_size_key = str(min_size_key).upper()

    min_size = 0.1  # default minimum
    try:
        if min_size_key:
            ticker_settings = getattr(config, "TICKER_SETTINGS", {}).get(min_size_key, {})
            min_size = float(ticker_settings.get("min_size", min_size))
    except Exception:
        min_size = 0.1

    if size < min_size:
        size = float(min_size)

    # 8) Final safety checks
    if size <= 0:
        return {"blocked": True, "reason": "computed_size_nonpositive"}

    if remaining_ticker_equity is not None:
        try:
            max_size_for_remaining_equity = math.floor(((remaining_ticker_equity * leverage) / entry) * 100) / 100
        except Exception:
            return {"blocked": True, "reason": "equity_recalculation_failed"}
        if max_size_for_remaining_equity > 0 and size > max_size_for_remaining_equity:
            size = max_size_for_remaining_equity
        if size < min_size:
            return {
                "blocked": True,
                "reason": "insufficient_ticker_capacity_for_min_size",
                "ticker": ticker_key,
            }

    try:
        actual_equity_used = (float(size) * entry) / leverage
    except Exception:
        return {"blocked": True, "reason": "equity_recalculation_failed"}
    if remaining_ticker_equity is not None and actual_equity_used - remaining_ticker_equity > 1e-9:
        return {
            "blocked": True,
            "reason": "ticker_capacity_exhausted",
            "ticker": ticker_key,
        }

    # 9) Return final sizing
    return {
        "blocked": False,
        "size": float(round(size, 2)),
        "exposure": float(round(float(size) * entry, 2)),
        "equity_used": float(round(actual_equity_used, 2)),
        "min_size": float(min_size),
        "open_positions": int(ticker_usage["open_count"]),
        "equity_used_by_ticker": float(round(ticker_usage["equity_used"], 2)),
        "remaining_ticker_equity": (
            None if remaining_ticker_equity is None else float(round(max(0.0, remaining_ticker_equity - actual_equity_used), 2))
        ),
    }
