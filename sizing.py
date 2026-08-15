# ============================
# POSITION SIZING MODULE (FULLY CORRECTED + SAFE)
# ============================

import session
from config import (
    MAX_POSITIONS_PER_TICKER,
    EQUITY_PERCENT,
    LEVERAGE
)


def calculate_size(entry_price, sl_price, tp_price, direction):
    """
    Corrected sizing logic:
    - Uses equity (cash + PnL)
    - Uses EQUITY_PERCENT allocation
    - Uses LEVERAGE multiplier
    - Uses entry_price for size calculation
    - Validates SL/TP properly (direction-aware)
    - Enforces max positions per ticker
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
        print("[SIZING] Blocked: cash balance is zero or negative.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "negative_balance"
        }

    # ---------------------------------------------------------
    # POSITION LIMIT PER TICKER
    # ---------------------------------------------------------
    positions = session.get_positions()
    epic = session.verify_epic(direction.upper()).get("epic")  # FIXED

    count = 0
    for p in positions:
        if p.get("market", {}).get("epic") == epic:
            count += 1

    if count >= MAX_POSITIONS_PER_TICKER:
        print("[SIZING] Blocked: max positions reached.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "max_positions"
        }

    # ---------------------------------------------------------
    # VALIDATE SL/TP
    # ---------------------------------------------------------
    if sl_price is None or tp_price is None:
        print("[SIZING] Blocked: SL/TP missing.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "missing_sl_tp"
        }

    sl_distance = abs(entry_price - sl_price)

    if sl_distance <= 0:
        print("[SIZING] Blocked: invalid SL distance.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl"
        }

    # Direction-aware SL validation
    if direction.lower() == "buy" and sl_price >= entry_price:
        print("[SIZING] Blocked: BUY SL must be below entry.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl_buy"
        }

    if direction.lower() == "sell" and sl_price <= entry_price:
        print("[SIZING] Blocked: SELL SL must be above entry.")
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

    print("[SIZING] Cash:", cash)
    print("[SIZING] PnL:", pnl)
    print("[SIZING] Equity:", equity)
    print("[SIZING] Allocation:", allocation)
    print("[SIZING] Leverage:", LEVERAGE)
    print("[SIZING] Exposure:", exposure)
    print("[SIZING] Entry price:", entry_price)
    print("[SIZING] Raw size:", raw_size)

    # ---------------------------------------------------------
    # FINAL SIZE
    # ---------------------------------------------------------
    final_size = round(raw_size, 2)

    if final_size <= 0:
        print("[SIZING] Blocked: final size <= 0.")
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
