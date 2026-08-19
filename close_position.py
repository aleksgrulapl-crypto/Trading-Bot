# ============================
# CLOSE POSITION MODULE (REVERTED — OLD LOGIC, NO MERGING)
# ============================

import session
from auth import auth
from utils import timestamp, calculate_profit_loss
from trade_log import log_closed_trade
from config import API_POSITIONS


def close_position(position_id):
    """
    Reverted close logic:
    - Use the exact ID passed from the dashboard
    - Compute exit price + pnl BEFORE closing
    - Log a simple CLOSED trade row
    - Do NOT merge with OPEN trades
    - Do NOT re-read closed positions from API
    """

    auth.ensure_token()

    # -----------------------------------------
    # 1. Get current enriched positions
    # -----------------------------------------
    raw_positions = session.get_positions() or []
    positions = session.enrich_positions(raw_positions)

    # Find the position we are closing
    pos = None
    for p in positions:
        if str(p.get("id")) == str(position_id):
            pos = p
            break

    if not pos:
        print(f"[CLOSE] Position {position_id} not found in current positions.", flush=True)
        return {
            "status": "error",
            "message": f"Position {position_id} not found."
        }

    # -----------------------------------------
    # 2. Extract fields BEFORE closing
    # -----------------------------------------
    ticker = pos.get("ticker")
    epic = pos.get("epic")
    direction = pos.get("direction")
    size = pos.get("size")
    entry_price = pos.get("price")
    exit_price = pos.get("current_price")

    pnl = calculate_profit_loss(direction, entry_price, exit_price, size)

    # -----------------------------------------
    # 3. Call Capital.com close endpoint
    # -----------------------------------------
    url = f"{API_POSITIONS}/{position_id}/close"
    response = session.request("POST", url, json={})

    if not response or response.status_code != 200:
        print(f"[ERROR] Failed to close position {position_id}", flush=True)
        print(f"[ERROR] Response: {response.text if response else 'No response'}", flush=True)
        return {
            "status": "error",
            "message": f"Failed to close position {position_id}"
        }

    # -----------------------------------------
    # 4. Log CLOSED trade (simple row)
    # -----------------------------------------
    log_closed_trade(
        ticker=ticker,
        epic=epic,
        deal_id=position_id,          # keep ID exactly as dashboard sees it
        side=direction,
        size=size,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        sl=None,
        tp=None,
        timeframe=None,
        time_entered=None,
        timestamp=timestamp()
    )

    # -----------------------------------------
    # 5. Update system status
    # -----------------------------------------
    session.update_last_trade()

    print(f"[TRADE CLOSED] {ticker} @ {exit_price} → PnL {pnl}", flush=True)

    return {
        "status": "success",
        "message": f"Position {position_id} closed."
    }
