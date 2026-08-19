# ============================
# CLOSE POSITION MODULE (LOCKED + CORRECT ENDPOINT FOR SHARES)
# ============================

import session
from auth import auth
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_POSITIONS


def _find_enriched_position(position_id):
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions) or []
    for p in enriched:
        if str(p.get("id")) == str(position_id):
            return p
    return None


def _find_open_trade(deal_id):
    log = load_raw_log() or []
    for entry in reversed(log):
        if entry.get("status") == "OPEN" and str(entry.get("dealId")) == str(deal_id):
            return entry
    return None


def close_position(position_id):

    # Prevent scheduler from interfering with session during close
    with auth.lock:

        auth.ensure_token()

        pos = _find_enriched_position(position_id)
        open_trade = _find_open_trade(position_id)

        ticker = pos.get("ticker") if pos else (open_trade.get("ticker") if open_trade else None)
        epic = pos.get("epic") if pos else (open_trade.get("epic") if open_trade else None)
        direction = pos.get("direction") if pos else (open_trade.get("side") if open_trade else None)
        size = pos.get("size") if pos else (open_trade.get("size") if open_trade else None)
        entry_price = pos.get("price") if pos else (open_trade.get("entry_price") if open_trade else None)
        current_price = pos.get("current_price") if pos else None

        pnl = None
        try:
            if direction and entry_price is not None and current_price is not None and size is not None:
                if direction.upper() == "BUY":
                    pnl = (current_price - entry_price) * float(size)
                else:
                    pnl = (entry_price - current_price) * float(size)
        except Exception as e:
            print(f"[CLOSE] Failed to compute PnL: {e}", flush=True)

        sl = open_trade.get("sl") if open_trade else None
        tp = open_trade.get("tp") if open_trade else None
        timeframe = open_trade.get("timeframe") if open_trade else None
        time_entered = open_trade.get("time_entered") if open_trade else None

        # ---------------------------------------------------------
        # CORRECT CLOSE ENDPOINT FOR SHARES (instrumentType = SHARES)
        # ---------------------------------------------------------
        try:
            url = f"{API_POSITIONS}/{position_id}/close"

            print(f"[CLOSE] URL → {url}", flush=True)

            response = session.request("POST", url, json={})

            if not response:
                print("[CLOSE] No response from /close endpoint", flush=True)
                return {"status": "error", "message": "no_response"}

            print(f"[CLOSE] STATUS → {response.status_code}", flush=True)
            print(f"[CLOSE] RESPONSE → {response.text}", flush=True)

            if response.status_code != 200:
                return {"status": "error", "message": response.text}

        except Exception as e:
            print(f"[CLOSE] Exception: {e}", flush=True)
            return {"status": "error", "message": str(e)}

        # ---------------------------------------------------------
        # LOG CLOSED TRADE
        # ---------------------------------------------------------
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
            print(f"[CLOSE] Failed to log CLOSED trade: {e}", flush=True)

        session.update_last_trade()

        return {"status": "success", "message": f"Position {position_id} closed."}
