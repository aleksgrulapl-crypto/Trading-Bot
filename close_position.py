# ============================
# CLOSE POSITION MODULE (RESTORED PIPELINE — RAW POSITIONS, SIMPLE CLOSE)
# ============================

import session
from trade_log import log_close
from utils import timestamp
from config import API_POSITIONS


def close_position(position_id):
    """
    Close a position using Capital.com API and log a CLOSED trade
    using the restored trade_log format (OPEN updated in place).
    """

    # 1) Fetch RAW positions (no enrich_positions)
    raw_positions = session.get_positions() or []

    target = None
    for p in raw_positions:
        # Capital.com uses dealId for closing
        if str(p.get("dealId")) == str(position_id):
            target = p
            break

    if not target:
        print(f"[CLOSE] Position {position_id} not found in RAW positions.")
        return {"status": "error", "reason": "position_not_found"}

    ticker = target.get("instrumentName") or target.get("epic") or "UNKNOWN"
    epic = target.get("epic")
    deal_id = target.get("dealId")

    # BUY or SELL
    direction = target.get("direction") or target.get("side")
    if direction:
        direction = direction.upper()

    size = target.get("size")

    # RAW Capital.com entry price
    entry_price = target.get("openLevel")

    sl = target.get("stopLevel")
    tp = target.get("limitLevel")

    # 2) Correct Capital.com close endpoint (RESTORED)
    url = f"{API_POSITIONS}/{deal_id}/close"
    print(f"[CLOSE] Closing dealId {deal_id} ({ticker})...", flush=True)

    r = session.request("POST", url, json={})

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed: {r.status_code if r else 'no response'}")
        try:
            print(f"[CLOSE] Response: {r.text}")
        except:
            pass
        return {"status": "error", "reason": "close_failed"}

    data = r.json() if r.content else {}

    # 3) Extract exit price + pnl (RESTORED)
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

    # 4) Log CLOSED trade (RESTORED FORMAT)
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
        timeframe=None
    )

    print(
        f"[CLOSE] CLOSED TRADE LOGGED → {ticker} {direction} "
        f"size={size} exit={exit_price} pnl={pnl}",
        flush=True,
    )

    return {
        "status": "ok",
        "dealId": deal_id,
        "exit_price": exit_price,
        "pnl": pnl
    }
