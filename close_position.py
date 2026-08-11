# ============================
# CLOSE POSITION MODULE (FINAL)
# ============================

import session
from auth import auth
from trade_log import log_close
from utils import timestamp
from config import API_POSITIONS

def close_position(position_id):
    """
    Close a position using Capital.com API + log closed trade.
    """

    # Ensure CST/XST are valid
    auth.ensure_token()

    try:
        # Correct endpoint
        url = f"{API_POSITIONS}/{position_id}/close"

        # Correct authenticated request wrapper
        response = session.request("POST", url, json={})

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text}"
            }

        # Fetch updated positions
        positions = session.get_positions()

        # Find closed position details
        for p in positions:
            if str(p["id"]) == str(position_id):
                ticker = p["ticker"]
                size = p["size"]
                close_price = p["current_price"]
                pnl = p["profit"]

                # Log closed trade
                log_close(
                    ticker=ticker,
                    size=size,
                    close_price=close_price,
                    pnl=pnl,
                    timestamp=timestamp()
                )

                break

        # Update system status
        session.update_last_trade()

        return {
            "status": "success",
            "message": f"Position {position_id} closed."
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
