# ============================
# CLOSE POSITION MODULE (FINAL + TRAIL-SL READY + CONSISTENT)
# ============================

import session
from auth import auth
from trade_log import load_raw_log, save_log
from utils import timestamp
from config import API_POSITIONS
from history import get_closed_trade_by_deal


def close_position(position_id):
    """
    Close a position using Capital.com API + log closed trade.
    Includes:
    - Correct exit price
    - Correct PnL
    - Symbol + EPIC + dealId logging
    - Direction + entry_price logging (required for trailing SL)
    - Dashboard state refresh
    - Safe error handling
    """

    auth.ensure_token()

    try:
        # -----------------------------
        # Snapshot BEFORE close (for symbol/epic/entry)
        # -----------------------------
        raw_positions_before = session.get_positions()
        enriched_before = session.enrich_positions(raw_positions_before)

        original = None
        for p in enriched_before:
            if str(p["id"]) == str(position_id):
                original = p
                break

        if not original:
            print(f"[WARN] Position {position_id} not found before close (logging will rely on history only).")

        # -----------------------------
        # Send close request
        # -----------------------------
        url = f"{API_POSITIONS}/{position_id}/close"
        response = session.request("POST", url, json={})

        if not response or response.status_code != 200:
            print(f"[ERROR] Failed to close position {position_id}")
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text if response else 'No response'}"
            }

        # -----------------------------
        # Fetch closed trade from Capital history
        # -----------------------------
        closed_entry = get_closed_trade_by_deal(position_id)

        if closed_entry:
            # If we had original snapshot, fill any missing fields
            if original:
                if closed_entry.get("ticker") is None:
                    closed_entry["ticker"] = original.get("ticker")
                if closed_entry.get("epic") is None:
                    closed_entry["epic"] = original.get("epic")
                if closed_entry.get("entry_price") is None:
                    closed_entry["entry_price"] = original.get("entry_price")
                if closed_entry.get("size") in (None, 0.0):
                    closed_entry["size"] = original.get("size")

            # Append CLOSED event to raw log
            log = load_raw_log()
            log.append(closed_entry)
            save_log(log)

            print(f"[TRADE CLOSED] {closed_entry['ticker']} @ {closed_entry['exit_price']} → PnL {closed_entry['pnl']}")
        else:
            print(f"[WARN] No closed history found for {position_id} (trade will remain OPEN in log).")

        # -----------------------------
        # Refresh shared dashboard state
        # -----------------------------
        raw_positions = session.get_positions()
        enriched_positions = session.enrich_positions(raw_positions)

        session.shared_state["positions"] = enriched_positions
        session.shared_state["account"] = session.enrich_account(session.get_account())
        session.shared_state["trade_log"] = session.shared_state["trade_log"]
        session.shared_state["daily_report"] = session.get_daily_report()

        # -----------------------------
        # Update system status
        # -----------------------------
        session.update_last_trade()

        return {
            "status": "success",
            "ticker": closed_entry.get("ticker") if closed_entry else (original.get("ticker") if original else None),
            "epic": closed_entry.get("epic") if closed_entry else (original.get("epic") if original else None),
            "dealId": position_id,
            "direction": closed_entry.get("side") if closed_entry else (original.get("side") if original else None),
            "size": closed_entry.get("size") if closed_entry else (original.get("size") if original else None),
            "entry_price": closed_entry.get("entry_price") if closed_entry else (original.get("entry_price") if original else None),
            "price": closed_entry.get("exit_price") if closed_entry else None,
            "pnl": closed_entry.get("pnl") if closed_entry else None,
            "message": f"Position {position_id} closed."
        }

    except Exception as e:
        print(f"[ERROR] Exception closing position {position_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
