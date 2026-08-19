# ============================
# CLOSE POSITION MODULE (EXECUTION-BASED LOGGING)
# ============================

import session
from auth import auth
from trade_log import log_closed_trade
from utils import timestamp
from config import API_POSITIONS
import time


def close_position(deal_id):
    """
    Close a position using Capital.com API and log a CLOSED trade
    using execution data from the close response (exit price + realised PnL).
    """

    auth.ensure_token()

    try:
        # Snapshot current positions to get context (ticker, epic, size, SL/TP)
        raw_positions = session.get_positions() or []
        enriched_positions = session.enrich_positions(raw_positions)

        context = None
        for p in enriched_positions:
            if str(p.get("id")) == str(deal_id):
                context = p
                break

        if not context:
            print(f"[WARN] No context found for dealId {deal_id} before close.", flush=True)

        # Correct endpoint: close by dealId
        url = f"{API_POSITIONS}/{deal_id}/close"

        # Capital.com typically accepts an empty JSON body for this
        response = session.request("POST", url, json={})

        if not response or response.status_code != 200:
            print(f"[ERROR] Failed to close position {deal_id}", flush=True)
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text if response else 'No response'}",
            }

        # Parse execution details from response
        try:
            data = response.json()
        except Exception as e:
            print(f"[ERROR] Failed to parse close response for {deal_id}: {e}", flush=True)
            data = {}

        exit_price = data.get("closeLevel") or data.get("level") or None
        pnl = data.get("profitLoss") or data.get("pnl") or None

        # Fallbacks from context if response is incomplete
        ticker = context.get("ticker") if context else None
        epic = context.get("epic") if context else None
        size = context.get("size") if context else None
        sl = context.get("stopLevel") if context else None
        tp = context.get("limitLevel") if context else None

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
            sl=sl,
            tp=tp,
            timeframe=None,
            time_entered=None,
            timestamp=ts,
        )

        print(f"[TRADE CLOSED] {ticker} @ {exit_price} → PnL £{pnl}", flush=True)

        # Refresh shared state (positions will no longer include this dealId)
        time.sleep(0.2)
        session.shared_state["positions"] = session.enrich_positions(session.get_positions() or [])
        session.shared_state["account"] = session.enrich_account(session.get_account() or {})
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
