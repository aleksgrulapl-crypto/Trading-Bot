# ============================
# CLOSE POSITION MODULE (CFD-CORRECT ENDPOINT)
# ============================

import session
from auth import auth
from trade_log import log_closed_trade
from utils import timestamp
from config import API_POSITIONS


def close_position(deal_id):
    """
    Correct CFD close:
    POST /positions/close
    Payload: { "dealId": "<dealId>" }
    """

    auth.ensure_token()

    try:
        # ⭐ Correct CFD endpoint
        url = f"{API_POSITIONS}/close"

        # ⭐ Correct CFD payload
        payload = {"dealId": deal_id}

        response = session.request("POST", url, json=payload)

        if not response or response.status_code != 200:
            print(f"[ERROR] Failed to close position {deal_id}", flush=True)
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text if response else 'No response'}",
            }

        # ⭐ Allow Capital.com to update the position list
        import time
        time.sleep(0.5)

        # Refresh positions
        raw_positions = session.get_positions()
        enriched_positions = session.enrich_positions(raw_positions)

        # Find closed position details
        closed = None
        for p in enriched_positions:
            if str(p.get("dealId")) == str(deal_id) or str(p.get("id")) == str(deal_id):
                closed = p
                break

        # If not found, still return success
        if not closed:
            print(f"[WARN] Closed position {deal_id} not found in updated list.", flush=True)
            session.update_last_trade()
            return {
                "status": "success",
                "message": f"Position {deal_id} closed (details unavailable).",
            }

        ticker = closed.get("ticker")
        epic = closed.get("epic")
        size = closed.get("size")
        exit_price = closed.get("current_price")
        pnl = closed.get("profit")

        ts = timestamp()

        log_closed_trade(
            ticker=ticker,
            epic=epic,
            deal_id=deal_id,
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
            "message": f"Position {deal_id} closed.",
        }

    except Exception as e:
        print(f"[ERROR] Exception closing position {deal_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": str(e),
        }
