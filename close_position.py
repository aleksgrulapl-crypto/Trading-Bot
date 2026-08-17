# ============================
# CLOSE POSITION MODULE (FINAL STRUCTURE-FIXED VERSION)
# ============================

import session
from trade_log import log_close
from utils import timestamp


def close_position(position_id):
    """
    Close a position on Capital.com and log the CLOSED trade directly
    into the persistent trade log (via log_close).
    This version correctly understands the structure returned by get_positions().
    """

    # 1) Fetch current positions (raw structure from Capital.com)
    positions = session.get_positions() or []
    target_pos = None
    target_mkt = None

    for item in positions:
        pos = item.get("position", {})
        mkt = item.get("market", {})

        deal_id = pos.get("dealId") or pos.get("positionId")
        if str(deal_id) == str(position_id):
            target_pos = pos
            target_mkt = mkt
            break

    if not target_pos:
        print(f"[CLOSE] Position {position_id} not found in raw positions.")
        return {"status": "error", "reason": "position_not_found"}

    # 2) Extract fields from the raw position/market
    epic = target_mkt.get("epic")
    ticker = target_mkt.get("symbol") or epic

    deal_id = target_pos.get("dealId") or target_pos.get("positionId")
    direction = target_pos.get("direction") or target_pos.get("side")
    size = target_pos.get("size")
    entry_price = target_pos.get("level") or target_pos.get("openLevel")
    currency = target_pos.get("currency") or "USD"

    sl = target_pos.get("stopLevel")
    tp = target_pos.get("profitLevel") or target_pos.get("limitLevel")

    # 3) Send close request
    print(f"[CLOSE] Closing position {position_id} ({ticker})...", flush=True)
    r = session.request("DELETE", f"{session.API_POSITIONS}/{position_id}")

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed: {r.status_code if r else 'no response'}", flush=True)
        try:
            print(f"[CLOSE] Response: {r.text}", flush=True)
        except Exception:
            pass
        return {"status": "error", "reason": "close_failed"}

    data = r.json() if r.content else {}

    # 4) Extract exit price + PnL from response or fallback to position
    exit_price = (
        data.get("closeLevel")
        or data.get("level")
        or target_pos.get("closeLevel")
        or target_pos.get("level")
    )

    pnl = (
        data.get("profitLoss")
        or target_pos.get("profitLoss")
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

    # 5) Log CLOSED trade
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
