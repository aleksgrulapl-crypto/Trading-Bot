# ============================
# CLOSE POSITION MODULE (RESTORED + MODERNISED)
# ============================

import session
from auth import auth
from trade_log import log_close
from utils import timestamp
from config import API_POSITIONS

def close_position(position_id):
    """
    Close a position using Capital.com API + log closed trade.
    Restored behaviour from dd25e77 + modern improvements.
    """

    auth.ensure_token()

    try:
        url = f"{API_POSITIONS}/{position_id}/close"

        response = session.request("POST", url, json={})

        if not response or response.status_code != 200:
            print(f"[ERROR] Failed to close position {position_id}")
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text if response else 'No response'}"
            }

        # Fetch updated positions
        raw_positions = session.get_positions()
        enriched_positions = session.enrich_positions(raw_positions)

        # Find closed position
        closed = None
        for p in enriched_positions:
            if str(p["id"]) == str(position_id):
                closed = p
                break

        if not closed:
            print(f"[WARN] Closed position {position_id} not found in updated list.")
            session.update_last_trade()
            return {
                "status": "success",
                "message": f"Position {position_id} closed (details unavailable)."
            }

        ticker = closed["ticker"]
        size = closed["size"]
        close_price = closed["current_price"]
        pnl = closed["profit"]

        # Log closed trade
        log_close(
            ticker=ticker,
            size=size,
            close_price=close_price,
            pnl=pnl,
            timestamp=timestamp()
        )

        print(f"[TRADE CLOSED] {ticker} @ {close_price} → PnL £{pnl}")

        # Update shared state
        session.shared_state["positions"] = enriched_positions
        session.shared_state["account"] = session.enrich_account(session.get_account())
        session.shared_state["trade_log"] = session.shared_state["trade_log"]  # already updated by log_close
        session.shared_state["daily_report"] = session.get_daily_report()

        # Update system status
        session.update_last_trade()

        return {
            "status": "success",
            "ticker": ticker,
            "size": size,
            "price": close_price,
            "pnl": pnl,
            "message": f"Position {position_id} closed."
        }

    except Exception as e:
        print(f"[ERROR] Exception closing position {position_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
