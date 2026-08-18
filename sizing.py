# ============================
# SIZING MODULE (FINAL — 50% AVAILABLE + LEVERAGE + SAFE SL/TP)
# ============================

import session
import config


def calculate_size(entry_price, sl_price, tp_price, direction):
    """
    Calculate position size using:
    - 50% of AVAILABLE equity
    - Leverage multiplier
    - Min size enforcement
    - SL/TP safety validation
    """

    # ----------------------------------------
    # 1) Fetch account (enriched)
    # ----------------------------------------
    account = session.enrich_account(session.get_account())

    available = account.get("available", 0)
    if available <= 0:
        return {
            "blocked": True,
            "reason": "no_available_margin"
        }

    # ----------------------------------------
    # 2) Apply your rule: 50% of AVAILABLE
    # ----------------------------------------
    equity_to_use = available * config.EQUITY_PERCENT  # 0.50

    # ----------------------------------------
    # 3) Apply leverage
    # ----------------------------------------
    exposure = equity_to_use * config.LEVERAGE

    # ----------------------------------------
    # 4) Convert exposure → size
    # ----------------------------------------
    raw_size = exposure / entry_price
    size = round(raw_size, 2)

    # ----------------------------------------
    # 5) Enforce min size per ticker
    # ----------------------------------------
    ticker_settings = config.TICKER_SETTINGS.get(
        session.shared_state.get("last_symbol", ""), {}
    )
    min_size = ticker_settings.get("min_size", 0.1)

    if size < min_size:
        size = min_size

    # ----------------------------------------
    # 6) SL/TP validation (Capital.com rejects invalid SL)
    # ----------------------------------------
    # For BUY: SL < entry < TP
    # For SELL: TP < entry < SL

    if direction.lower() == "buy":
        if not (sl_price < entry_price < tp_price):
            return {
                "blocked": True,
                "reason": "invalid_sl_tp_buy"
            }

    if direction.lower() == "sell":
        if not (tp_price < entry_price < sl_price):
            return {
                "blocked": True,
                "reason": "invalid_sl_tp_sell"
            }

    # ----------------------------------------
    # 7) Return final sizing
    # ----------------------------------------
    return {
        "blocked": False,
        "size": size
    }
