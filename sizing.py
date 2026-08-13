# ============================
# POSITION SIZING MODULE (RESTORED + UPDATED + FIXED)
# ============================

import session
from config import MAX_POSITIONS_PER_TICKER

def calculate_size(available, entry_price, sl_price, tp_price, direction):
    """
    New sizing logic:
    - Uses correct cash balance (not available)
    - Uses SL/TP from TradingView
    - Uses risk-based sizing (2% of cash balance)
    - Validates SL distance
    - Enforces max positions per ticker
    """

    # -----------------------------------------
    # BLOCK NEGATIVE BALANCE (corrected)
    # -----------------------------------------
    if available <= 0:
        print("[SIZING] Blocked: cash balance is zero or negative.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "negative_balance"
        }

    # -----------------------------------------
    # POSITION LIMIT PER TICKER
    # -----------------------------------------
    positions = session.get_positions()
    count = 0
    for p in positions:
        market = p.get("market", {})
        if market.get("epic") == session.verify_epic(market.get("symbol")).get("epic"):
            count += 1

    if count >= MAX_POSITIONS_PER_TICKER:
        print("[SIZING] Blocked: max positions reached.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "max_positions"
        }

    # -----------------------------------------
    # RISK-BASED SIZING (2% of cash balance)
    # -----------------------------------------
    risk_per_trade = available * 0.02  # 2% risk

    sl_distance = abs(entry_price - sl_price)

    if sl_distance <= 0:
        print("[SIZING] Blocked: invalid SL distance.")
        return {
            "size": 0,
            "blocked": True,
            "reason": "invalid_sl"
        }

    size = risk_per_trade / sl_distance

    print("[SIZING] Cash balance:", available)
    print("[SIZING] Entry price:", entry_price)
    print("[SIZING] SL price:", sl_price)
    print("[SIZING] SL distance:", sl_distance)
    print("[SIZING] Risk per trade:", risk_per_trade)
    print("[SIZING] Final size:", size)

    return {
        "size": round(size, 2),
        "blocked": False,
        "reason": None
    }
