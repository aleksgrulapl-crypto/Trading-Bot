# ============================
# SIZING MODULE (FINAL — RESTORED PRE‑TIMEFRAME LOGIC)
# ============================

from config import EQUITY_PERCENT, LEVERAGE
from utils import timestamp

def calculate_size(available, entry_price, sl_price, tp_price, direction):
    """
    Returns a safe, validated position size.
    Blocks trades when:
    - available balance is negative
    - entry price invalid
    - SL/TP invalid
    - calculated size is too small
    """

    # -----------------------------------------
    # BLOCK NEGATIVE BALANCE
    # -----------------------------------------
    if available <= 0:
        print("[SIZING] Blocked: available balance is negative.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "negative_balance"
        }

    # -----------------------------------------
    # VALIDATE ENTRY PRICE
    # -----------------------------------------
    if not entry_price or entry_price <= 0:
        print("[SIZING] Blocked: invalid entry price.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_entry"
        }

    # -----------------------------------------
    # VALIDATE SL/TP
    # -----------------------------------------
    if sl_price is None or tp_price is None:
        print("[SIZING] Blocked: SL/TP missing.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "missing_sl_tp"
        }

    # -----------------------------------------
    # ALLOCATION (RESTORED)
    # -----------------------------------------
    allocation = available * EQUITY_PERCENT
    print(f"[SIZING] Allocation: {allocation}")

    # -----------------------------------------
    # LEVERAGE (RESTORED)
    # -----------------------------------------
    exposure = allocation * LEVERAGE
    print(f"[SIZING] Exposure (allocation * leverage): {exposure}")

    # -----------------------------------------
    # RAW SIZE (RESTORED)
    # -----------------------------------------
    raw_size = exposure / entry_price
    print(f"[SIZING] Raw size: {raw_size}")

    # -----------------------------------------
    # BLOCK NEGATIVE SIZE
    # -----------------------------------------
    if raw_size <= 0:
        print("[SIZING] Blocked: raw size is negative or zero.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "negative_size"
        }

    # -----------------------------------------
    # MINIMUM SIZE CHECK
    # -----------------------------------------
    if raw_size < 0.1:
        print("[SIZING] Blocked: size too small (<0.1).")
        return {
            "size": 0,
            "blocked": True,
            "reason": "too_small"
        }

    # -----------------------------------------
    # FINAL SIZE
    # -----------------------------------------
    final_size = round(raw_size, 2)
    print(f"[SIZING] Final size: {final_size}")

    return {
        "size": final_size,
        "blocked": False,
        "reason": None
    }
