# ============================
# CLOSE POSITION MODULE (FINAL + FULLY COMPATIBLE)
# ============================

import session
from trade_log import log_close
from utils import timestamp


def close_position(position_id):
    """
    Close a position on Capital.com and log the CLOSED trade directly
    into the persistent trade log (via log_close).
    This version is fully compatible with the merge engine and dashboard.
    """

    # -----------------------------
    # 1) Fetch current positions
    # -----------------------------
    positions = session.get_positions() or []
    target = None

    for p in positions:
        if str(p.get("dealId") or p.get("positionId")) == str(position_id):
            target = p
            break

    if not target:
        print(f"[CLOSE] Position {position_id} not found.")
        return {"status": "error", "reason": "position_not_found"}

    epic = target.get("epic")
    deal_id = target.get("dealId")
    direction = target.get("direction") or target.get("side")
    size = target.get("size")
    entry_price = target.get("level") or target.get("openLevel")
    currency = target.get("currency") or "USD"
    ticker = target.get("instrumentName") or epic

    sl = target.get("stopLevel")
    tp = target.get("limitLevel")

    # -----------------------------
    # 2) Send close request
    # -----------------------------
    print(f"[CLOSE] Closing position {position_id} ({ticker})...")
    r = session.close_position(position_id)

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed: {r.status_code if r else 'no response'}")
        return {"status": "error", "reason": "close_failed"}

    data = r.json() if r.content else {}

    # -----------------------------
    # 3) Extract exit price + PnL
    # -----------------------------
    exit_price = (
        data.get("closeLevel")
        or data.get("level")
        or target.get("closeLevel")
        or target.get("current_price")
    )

    pnl = (
        data.get("profitLoss")
        or target.get("profitLoss")
        or 0.0
    )

    try:
        exit_price = float(exit_price)
    except Exception:
        exit_price = None

    try:
        pnl = float(pnl)
    except Exception:
        pnl = 0.0

    close_ts = timestamp()

    # -----------------------------
    # 4) Log CLOSED trade (correct function)
    # -----------------------------
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
        f"size={size} exit={exit_price} pnl={pnl}"
    )

    return {
        "status": "ok",
        "dealId": deal_id,
        "exit_price": exit_price,
        "pnl": pnl
    }
