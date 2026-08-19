# ============================
# CLOSE POSITION MODULE (FINAL — CORRECT DEALID + EXIT PRICE FROM HISTORY)
# ============================

import session
from auth import auth
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_POSITIONS, API_HISTORY_TRANSACTIONS


def _find_enriched_position(position_id):
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions) or []
    for p in enriched:
        if str(p.get("id")) == str(position_id) or str(p.get("dealId")) == str(position_id):
            return p
    return None


def _find_open_trade(deal_id):
    log = load_raw_log() or []
    for entry in reversed(log):
        if entry.get("status") == "OPEN" and str(entry.get("dealId")) == str(deal_id):
            return entry
    return None


def _fetch_close_details_from_history(deal_id):
    """
    Pull exit price + realized PnL from Capital.com history.
    This is the ONLY reliable source for closed trade data.
    """
    try:
        # You can tune params (max, type) if needed
        url = f"{API_HISTORY_TRANSACTIONS}?max=100"
        r = session.request("GET", url)

        if not r or r.status_code != 200:
            print(f"[CLOSE] History fetch failed: {r.status_code if r else 'no_response'}", flush=True)
            return None, None

        data = r.json()
        transactions = data.get("transactions", [])

        exit_price = None
        pnl = None

        for tx in transactions:
            # Capital.com usually exposes either dealId or positionId here
            tx_deal_id = tx.get("dealId") or tx.get("positionId")
            if str(tx_deal_id) == str(deal_id):
                # Common fields: closeLevel / level / price, profitAndLoss / pnl
                exit_price = tx.get("closeLevel") or tx.get("level") or tx.get("price")
                pnl = tx.get("profitAndLoss") or tx.get("pnl")
                break

        return exit_price, pnl

    except Exception as e:
        print(f"[CLOSE] Exception while reading history: {e}", flush=True)
        return None, None


def close_position(position_id):
    """
    Close a position using Capital.com API + log closed trade.
    Uses the correct endpoint:
        POST /positions/{dealId}/close
    """

    # Ensure authenticated
    auth.ensure_token()

    # ---------------------------------------------------------
    # 1. SEND CLOSE REQUEST
    # ---------------------------------------------------------
    try:
        url = f"{API_POSITIONS}/{position_id}/close"
        print(f"[CLOSE] URL → {url}", flush=True)

        response = session.request("POST", url)

        if not response:
            print("[CLOSE] No response from close endpoint", flush=True)
            return {"status": "error", "message": "no_response"}

        print(f"[CLOSE] STATUS → {response.status_code}", flush=True)
        print(f"[CLOSE] RESPONSE → {response.text}", flush=True)

        if response.status_code != 200:
            return {"status": "error", "message": response.text}

    except Exception as e:
        print(f"[CLOSE] Exception during close: {e}", flush=True)
        return {"status": "error", "message": str(e)}

    # ---------------------------------------------------------
    # 2. LOOK UP OPEN TRADE + BASIC CONTEXT
    # ---------------------------------------------------------
    try:
        open_trade = _find_open_trade(position_id)
        pos = _find_enriched_position(position_id)  # may be None after close

        ticker = (pos.get("ticker") if pos else None) or (open_trade.get("ticker") if open_trade else None)
        epic = (pos.get("epic") if pos else None) or (open_trade.get("epic") if open_trade else None)
        direction = (pos.get("direction") if pos else None) or (open_trade.get("side") if open_trade else None)
        size = (pos.get("size") if pos else None) or (open_trade.get("size") if open_trade else None)
        entry_price = (pos.get("price") if pos else None) or (open_trade.get("entry_price") if open_trade else None)

        sl = open_trade.get("sl") if open_trade else None
        tp = open_trade.get("tp") if open_trade else None
        timeframe = open_trade.get("timeframe") if open_trade else None
        time_entered = open_trade.get("time_entered") if open_trade else None

        # ---------------------------------------------------------
        # 3. FETCH EXIT PRICE + REALIZED PnL FROM HISTORY
        # ---------------------------------------------------------
        exit_price, pnl = _fetch_close_details_from_history(position_id)

        print(f"[CLOSE] History exit_price={exit_price}, pnl={pnl}", flush=True)

        # ---------------------------------------------------------
        # 4. LOG CLOSED TRADE
        # ---------------------------------------------------------
        log_closed_trade(
            ticker=ticker,
            epic=epic,
            deal_id=position_id,
            side=direction,
            size=size,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            sl=sl,
            tp=tp,
            timeframe=timeframe,
            time_entered=time_entered,
            timestamp=timestamp()
        )

    except Exception as e:
        print(f"[CLOSE] Failed to log CLOSED trade: {e}", flush=True)

    session.update_last_trade()

    return {"status": "success", "message": f"Position {position_id} closed."}
