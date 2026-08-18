# ============================
# CLOSE POSITION MODULE (LOG + POSITIONS DRIVEN, MATCHES OLD FORMAT)
# ============================

import session
from trade_log import load_raw_log, log_close
from utils import timestamp
from config import API_POSITIONS


def close_position(position_id):
    """
    Close a position using Capital.com API and log a CLOSED trade.
    - Uses trade_log to find the OPEN entry (dealId = position_id)
    - Uses current positions to get entry_price + size
    - Uses close response for exit_price + pnl
    - Appends a CLOSED row (same structure as old working log)
    """

    # 1) Find the OPEN trade in the log
    log = load_raw_log()
    open_entry = None

    for t in log:
        if str(t.get("dealId")) == str(position_id) and t.get("exit_price") in (None, "—"):
            open_entry = t
            break

    if not open_entry:
        print(f"[CLOSE] No OPEN trade found for dealId {position_id}")
        return {"status": "error", "reason": "open_trade_not_found"}

    # Base details from the OPEN row (matches trade_log format)
    ticker = open_entry.get("ticker") or "UNKNOWN"
    epic = open_entry.get("epic")
    deal_id = open_entry.get("dealId")
    direction = open_entry.get("side")
    size = open_entry.get("size")
    entry_price = open_entry.get("entry_price")
    sl = open_entry.get("sl")
    tp = open_entry.get("tp")
    timeframe = open_entry.get("timeframe")

    # 2) Try to refine entry_price/size from live positions (optional, but keeps values consistent)
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions)

    for p in enriched:
        # match by dealId or fallback by epic/ticker
        if str(p.get("id")) == str(position_id) or str(p.get("dealId")) == str(position_id) or str(p.get("epic")) == str(epic):
            # these fields are how your dashboard shows open positions
            entry_price = p.get("price", entry_price)
            size = p.get("size", size)
            sl = p.get("stopLevel", sl)
            tp = p.get("limitLevel", tp)
            break

    # 3) Call Capital.com close endpoint
    url = f"{API_POSITIONS}/{deal_id}/close"
    print(f"[CLOSE] Closing {ticker} dealId={deal_id}...", flush=True)

    r = session.request("POST", url, json={})

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed: {r.status_code if r else 'no response'}")
        try:
            print(f"[CLOSE] Response: {r.text}")
        except:
            pass
        return {"status": "error", "reason": "close_failed"}

    data = r.json() if r.content else {}

    # 4) Extract exit price + pnl from CLOSE RESPONSE
    exit_price = data.get("closeLevel") or data.get("level")
    pnl = data.get("profitLoss")

    try:
        exit_price = float(exit_price)
    except:
        exit_price = None

    try:
        pnl = float(pnl)
    except:
        pnl = 0.0

    close_ts = timestamp()

    # 5) Append CLOSED trade (same structure as old working log)
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
