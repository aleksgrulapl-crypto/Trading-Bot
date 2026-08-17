# ============================
# CLOSE POSITION MODULE (RESTORED DIRECT CLOSE LOGIC)
# ============================

from datetime import datetime

import session
from config import API_POSITIONS
from trade_log import log_trade
from utils import timestamp


def close_position(position_id):
    """
    Close a position on Capital.com and log the CLOSED trade directly
    into trade_log.json, without relying on the history endpoint.
    """

    # 1) Fetch current positions to find the one we're closing
    positions = session.fetch_positions_from(API_POSITIONS) or []
    target = None

    for p in positions:
        if str(p.get("positionId") or p.get("dealId")) == str(position_id):
            target = p
            break

    if not target:
        print(f"[CLOSE] Position {position_id} not found in current positions.")
        return {"status": "error", "reason": "position_not_found"}

    epic = target.get("epic")
    deal_id = target.get("dealId") or target.get("positionId")
    direction = target.get("direction") or target.get("side")
    size = target.get("size") or target.get("quantity")
    entry_price = target.get("level") or target.get("openLevel")
    currency = target.get("currency") or "USD"
    ticker = target.get("instrumentName") or epic or "UNKNOWN"

    # 2) Close the position via Capital.com
    print(f"[CLOSE] Closing position {position_id} ({ticker})...")
    r = session.close_position(position_id)

    if not r or r.status_code not in (200, 202):
        print(f"[CLOSE] Close request failed for {position_id}: {r.status_code if r else 'no response'}")
        return {"status": "error", "reason": "close_failed", "code": r.status_code if r else None}

    data = r.json() if r.content else {}

    # 3) Derive exit price and PnL from response or snapshot
    exit_price = data.get("closeLevel") or data.get("level") or target.get("closeLevel")
    pnl = data.get("profitLoss") or target.get("profitLoss") or 0.0

    try:
        if exit_price is not None:
            exit_price = float(exit_price)
    except Exception:
        exit_price = None

    try:
        pnl = float(pnl)
    except Exception:
        pnl = 0.0

    close_ts = timestamp()

    # 4) Log CLOSED trade directly
    log_trade(
        ticker=ticker,
        epic=epic,
        deal_id=deal_id,
        side=direction,
        size=size,
        price=entry_price,
        sl=target.get("stopLevel"),
        tp=target.get("limitLevel"),
        timestamp=close_ts,
        timeframe=None,
        exit_price=exit_price,
        pnl=pnl,
        status="CLOSED",
        close_timestamp=close_ts,
        close_source="BOT_CLOSE",
        platform="Capital",
        currency=currency,
        notes=None
    )

    print(f"[CLOSE] CLOSED TRADE LOGGED → {ticker} {direction} size={size} exit={exit_price} pnl={pnl}")

    return {"status": "ok", "dealId": deal_id, "exit_price": exit_price, "pnl": pnl}
