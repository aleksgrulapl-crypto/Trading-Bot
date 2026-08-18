# ============================
# CLOSE POSITION MODULE (UNIFIED CLOSED LOGGING)
# ============================

import session
from auth import auth
from trade_log import log_closed_trade
from utils import timestamp
from config import API_POSITIONS


def close_position(position_id):
    """
    Close a position using Capital.com API and log a CLOSED trade
    as a separate row in unified format. No merging with OPEN.
    """

    auth.ensure_token()

    try:
        url = f"{API_POSITIONS}/{position_id}/close"
        response = session.request("POST", url, json={})

        if not response or response.status_code != 200:
            print(f"[ERROR] Failed to close position {position_id}", flush=True)
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text if response else 'No response'}",
            }

        raw_positions = session.get_positions()
        enriched_positions = session.enrich_positions(raw_positions)

        closed = None
        for p in enriched_positions:
            if str(p["id"]) == str(position_id):
                closed = p
                break

        if not closed:
            print(f"[WARN] Closed position {position_id} not found in updated list.", flush=True)
            session.update_last_trade()
            return {
                "status": "success",
                "message": f"Position {position_id} closed (details unavailable).",
            }

        ticker = closed.get("ticker")
        epic = closed.get("epic")
        size = closed.get("size")
        exit_price = closed.get("current_price")
        pnl = closed.get("profit")

        ts = timestamp()

        # We don't have entry_price/time_entered from positions; leave them None.
        log_closed_trade(
            ticker=ticker,
            epic=epic,
            deal_id=None,
            side="CLOSE",
            size=size,
            entry_price=None,
            exit_price=exit_price,
            pnl=pnl,
            sl=closed.get("stopLevel"),
            tp=closed.get("limitLevel"),
            timeframe=None,
            time_entered=None,
            timestamp=ts,
        )

        print(f"[TRADE CLOSED] {ticker} @ {exit_price} → PnL £{pnl}", flush=True)

        session.shared_state["positions"] = enriched_positions
        session.shared_state["account"] = session.enrich_account(session.get_account())
        session.shared_state["trade_log"] = session.shared_state["trade_log"]
        session.shared_state["daily_report"] = session.get_daily_report()

        session.update_last_trade()

        return {
            "status": "success",
            "ticker": ticker,
            "size": size,
            "price": exit_price,
            "pnl": pnl,
            "message": f"Position {position_id} closed.",
        }

    except Exception as e:
        print(f"[ERROR] Exception closing position {position_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": str(e),
        }
