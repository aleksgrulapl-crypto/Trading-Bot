# ============================
# CLOSE POSITION MODULE (FINAL — AUTH + LOG-DRIVEN + EXIT FIXED)
# ============================

import session
import requests
from trade_log import load_raw_log, log_close
from utils import timestamp
from config import API_POSITIONS


def close_position(position_id):
    """
    Close a position using Capital.com API and log a CLOSED trade.
    - Finds OPEN trade in trade_log (dealId = dealReference)
    - Sends authenticated close request (CST + X-SECURITY-TOKEN)
    - Uses close response for exit price + pnl
    - Appends CLOSED row (same structure as old working log)
    """

    # ---------------------------------------------------------
    # 1) Find the OPEN trade in the log
    # ---------------------------------------------------------
    log = load_raw_log()
    open_entry = None

    for t in log:
        if str(t.get("dealId")) == str(position_id) and t.get("exit_price") in (None, "—"):
            open_entry = t
            break

    if not open_entry:
        print(f"[CLOSE] No OPEN trade found for dealId {position_id}")
        return {"status": "error", "reason": "open_trade_not_found"}

    # Extract details from OPEN trade
    ticker = open_entry.get("ticker")
    epic = open_entry.get("epic")
    deal_id = open_entry.get("dealId")
    direction = open_entry.get("side")
    size = open_entry.get("size")
    entry_price = open_entry.get("entry_price")
    sl = open_entry.get("sl")
    tp = open_entry.get("tp")
    timeframe = open_entry.get("timeframe")

    # ---------------------------------------------------------
    # 2) Authenticated CLOSE request
    # ---------------------------------------------------------
    url = f"{API_POSITIONS}/{deal_id}/close"
    headers = session.auth_headers()  # CST + X-SECURITY-TOKEN

    print(f"[CLOSE] Closing {ticker} dealId={deal_id}...", flush=True)

    try:
        r = requests.post(url, json={}, headers=headers)
    except Exception as e:
        print(f"[CLOSE] Request exception: {e}", flush=True)
        return {"status": "error", "reason": "request_exception"}

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed: {r.status_code if r else 'no response'}")
        try:
            print(f"[CLOSE] Response: {r.text}")
        except:
            pass
        return {"status": "error", "reason": "close_failed"}

    data = r.json() if r.content else {}

    # ---------------------------------------------------------
    # 3) Extract exit price + pnl from CLOSE RESPONSE
    # ---------------------------------------------------------
    exit_price = (
        data.get("closeLevel")
        or data.get("level")
        or data.get("dealConfirmation", {}).get("closeLevel")
        or None
    )

    pnl = (
        data.get("profitLoss")
        or data.get("dealConfirmation", {}).get("profitLoss")
        or 0.0
    )

    try:
        exit_price = float(exit_price)
    except:
        exit_price = None

    try:
        pnl = float(pnl)
    except:
        pnl = 0.0

    close_ts = timestamp()

    # ---------------------------------------------------------
    # 4) Append CLOSED trade (exact old format)
    # ---------------------------------------------------------
    log_close(
        ticker=ticker,
        epic=epic,
        deal_id=deal_id,
        direction=direction,
        size=size,
        entry_price=entry_price,
        close_price=exit_price,
        pnl=pnl,
        sl=sl,
        tp=tp,
        timestamp=close_ts,
        timeframe=timeframe
    )

    print(
        f"[CLOSE] CLOSED TRADE LOGGED → {ticker} {direction} "
        f"size={size} entry={entry_price} exit={exit_price} pnl={pnl}",
        flush=True,
    )

    return {
        "status": "ok",
        "dealId": deal_id,
        "exit_price": exit_price,
        "pnl": pnl
    }
