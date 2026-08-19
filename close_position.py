# ============================
# CLOSE POSITION MODULE (DIAGNOSTIC + SAFE LOGGING)
# ============================

import session
from auth import auth
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_POSITIONS


def _find_enriched_position(position_id):
    """
    Find the enriched position (from session.enrich_positions)
    matching the given position_id (dealId).
    """
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions) or []

    for p in enriched:
        if str(p.get("id")) == str(position_id):
            return p

    return None


def _find_open_trade(deal_id):
    """
    Find the last OPEN trade in the log matching the given dealId.
    """
    log = load_raw_log() or []

    for entry in reversed(log):
        if entry.get("status") == "OPEN" and str(entry.get("dealId")) == str(deal_id):
            return entry

    return None


def close_position(position_id):
    """
    Close a position using Capital.com API and log a CLOSED trade
    in unified format, using both the current position snapshot
    and the existing OPEN trade from the log.
    """

    auth.ensure_token()

    # Snapshot BEFORE closing (for price/pnl)
    pos = _find_enriched_position(position_id)

    if not pos:
        print(f"[CLOSE] No enriched position found for id={position_id}", flush=True)

    # Try to find matching OPEN trade in the log
    open_trade = _find_open_trade(position_id)

    # Prepare fields for logging
    ticker = pos.get("ticker") if pos else (open_trade.get("ticker") if open_trade else None)
    epic = pos.get("epic") if pos else (open_trade.get("epic") if open_trade else None)
    direction = pos.get("direction") if pos else (open_trade.get("side") if open_trade else None)
    size = pos.get("size") if pos else (open_trade.get("size") if open_trade else None)
    entry_price = pos.get("price") if pos else (open_trade.get("entry_price") if open_trade else None)

    current_price = pos.get("current_price") if pos else None

    # Compute pnl if we have enough data
    pnl = None
    try:
        if direction and entry_price is not None and current_price is not None and size is not None:
            if direction.upper() == "BUY":
                pnl = (current_price - entry_price) * float(size)
            else:
                pnl = (entry_price - current_price) * float(size)
    except Exception as e:
        print(f"[CLOSE] Failed to compute PnL: {e}", flush=True)

    # SL/TP/timeframe/time_entered from open trade if available
    sl = open_trade.get("sl") if open_trade else None
    tp = open_trade.get("tp") if open_trade else None
    timeframe = open_trade.get("timeframe") if open_trade else None
    time_entered = open_trade.get("time_entered") if open_trade else None

    # --- Call Capital.com close endpoint (primary) ---
    try:
        url = f"{API_POSITIONS}/{position_id}/close"
        print(f"[CLOSE] URL → {url}", flush=True)

        response = session.request("POST", url, json={})

        if not response:
            print(f"[CLOSE] No response object returned for {url}", flush=True)
            return {
                "status": "error",
                "message": "no_response_from_close_endpoint"
            }

        print(f"[CLOSE] STATUS → {response.status_code}", flush=True)
        print(f"[CLOSE] RESPONSE → {response.text}", flush=True)

        # If primary endpoint fails with 404, try alternative close-position endpoint
        if response.status_code == 404:
            alt_url = f"{API_POSITIONS}/close-position"
            alt_payload = {"dealId": position_id}
            print(f"[CLOSE] 404 on primary, trying alt endpoint → {alt_url}", flush=True)
            print(f"[CLOSE] ALT PAYLOAD → {alt_payload}", flush=True)

            alt_response = session.request("POST", alt_url, json=alt_payload)

            if not alt_response:
                print(f"[CLOSE] No response from alt endpoint {alt_url}", flush=True)
                return {
                    "status": "error",
                    "message": "no_response_from_alt_close_endpoint"
                }

            print(f"[CLOSE] ALT STATUS → {alt_response.status_code}", flush=True)
            print(f"[CLOSE] ALT RESPONSE → {alt_response.text}", flush=True)

            if alt_response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"Alt close endpoint failed: {alt_response.text}"
                }

            response = alt_response  # treat alt as the effective close

        elif response.status_code != 200:
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text}",
            }

    except Exception as e:
        print(f"[ERROR] Exception during close for {position_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": str(e)
        }

    # --- Log CLOSED trade in unified format ---
    try:
        log_closed_trade(
            ticker=ticker,
            epic=epic,
            deal_id=position_id,
            side=direction,
            size=size,
            entry_price=entry_price,
            exit_price=current_price,
            pnl=pnl,
            sl=sl,
            tp=tp,
            timeframe=timeframe,
            time_entered=time_entered,
            timestamp=timestamp()
        )
    except Exception as e:
        print(f"[CLOSE] Failed to log CLOSED trade for {position_id}: {e}", flush=True)

    # Update system status
    session.update_last_trade()

    return {
        "status": "success",
        "message": f"Position {position_id} closed."
    }
