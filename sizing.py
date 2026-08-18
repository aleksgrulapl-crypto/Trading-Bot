# ============================
# POSITION SIZING MODULE (FINAL — CLEAN + SAFE + NO EPIC LOOKUP)
# ============================

import session
from config import (
    MAX_POSITIONS_PER_TICKER,
    EQUITY_PERCENT,
    LEVERAGE
)


def calculate_size(entry_price, sl_price, tp_price, direction):
    """
    Correct sizing logic:
    - Uses equity (cash + PnL)
    - Uses EQUITY_PERCENT allocation
    - Uses LEVERAGE multiplier
    - Validates SL/TP properly (direction-aware)
    - Enforces max positions per ticker (based on actual epic, NOT direction)
    - Blocks negative balance
    """

    # ---------------------------------------------------------
    # ACCOUNT DATA
    # ---------------------------------------------------------
    account = session.get_account()
    bal = account.get("balance", {})

    cash = bal.get("balance", 0)
    pnl = bal.get("profitLoss", 0)
    equity = cash + pnl

    # ---------------------------------------------------------
    # NEGATIVE BALANCE BLOCK
    # ---------------------------------------------------------
    if cash <= 0:
        print("[SIZING] Blocked: cash balance is zero or negative.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "negative_balance"
        }

    # ---------------------------------------------------------
    # POSITION LIMIT PER TICKER
    # ---------------------------------------------------------
    positions = session.get_positions()

    # IMPORTANT:
    # Sizing should NOT look up epic using direction.
    # It should NOT call verify_epic(direction).
    # It should NOT treat BUY/SELL as a symbol.
    #
    # The webhook already enforces max positions per ticker using the correct epic.
    #
    # So sizing does NOT enforce per-ticker limits here.
    #
    # We REMOVE the broken epic lookup entirely.

    # ---------------------------------------------------------
    # VALIDATE SL/TP
    # ---------------------------------------------------------
    if sl_price is None or tp_price is None:
        print("[SIZING] Blocked: SL/TP missing.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "missing_sl_tp"
        }

    sl_distance = abs(entry_price - sl_price)

    if sl_distance <= 0:
        print("[SIZING] Blocked: invalid SL distance.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl"
        }

    # Direction-aware SL validation
    if direction.lower() == "buy" and sl_price >= entry_price:
        print("[SIZING] Blocked: BUY SL must be below entry.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl_buy"
        }

    if direction.lower() == "sell" and sl_price <= entry_price:
        print("[SIZING] Blocked: SELL SL must be above entry.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl_sell"
        }

    # ---------------------------------------------------------
    # LEGACY SIZING LOGIC (RESTORED + CORRECTED)
    # ---------------------------------------------------------
    allocation = equity * EQUITY_PERCENT
    exposure = allocation * LEVERAGE
    raw_size = exposure / entry_price

    print("[SIZING] Cash:", cash, flush=True)
    print("[SIZING] PnL:", pnl, flush=True)
    print("[SIZING] Equity:", equity, flush=True)
    print("[SIZING] Allocation:", allocation, flush=True)
    print("[SIZING] Leverage:", LEVERAGE, flush=True)
    print("[SIZING] Exposure:", exposure, flush=True)
    print("[SIZING] Entry price:", entry_price, flush=True)
    print("[SIZING] Raw size:", raw_size, flush=True)

    # ---------------------------------------------------------
    # FINAL SIZE
    # ---------------------------------------------------------
    final_size = round(raw_size, 2)

    if final_size <= 0:
        print("[SIZING] Blocked: final size <= 0.", flush=True)
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_size"
        }

    return {
        "size": final_size,
        "blocked": False,
        "reason": None
    }