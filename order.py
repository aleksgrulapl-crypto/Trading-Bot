# ============================
# ORDER MODULE (REVERTED — OLD LOGIC, NO DEALID MAPPING)
# ============================

import session
from auth import auth
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_open_trade


def place_order(epic, direction, size, sl=None, tp=None, timeframe=None):
    """
    Reverted order logic:
    - Fetch market snapshot
    - Determine entry price
    - Send order immediately
    - Log OPEN trade immediately (no dealId dependency)
    - Do NOT map dealReference → dealId
    - Do NOT wait for positions to appear
    """

    auth.ensure_token()

    # -----------------------------------------
    # 1. MARKET SNAPSHOT
    # -----------------------------------------
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print(f"[ERROR] Market snapshot unavailable for {epic}", flush=True)
        return {"status": "error", "message": "Market snapshot unavailable"}

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print(f"[ERROR] Market prices unavailable for {epic}", flush=True)
        return {"status": "error", "message": "Price unavailable"}

    entry_price = offer if direction.lower() == "buy" else bid

    # -----------------------------------------
    # 2. BUILD ORDER PAYLOAD
    # -----------------------------------------
    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": float(size),
        "orderType": "MARKET",
        "level": None,
        "guaranteedStop": False,
    }

    if sl is not None:
        payload["stopLevel"] = float(sl)
    if tp is not None:
        payload["profitLevel"] = float(tp)

    print("[ORDER] Sending payload:", payload, flush=True)

    # -----------------------------------------
    # 3. SEND ORDER
    # -----------------------------------------
    response = session.request("POST", API_POSITIONS, json=payload)
    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})", flush=True)
        print(f"[ERROR] Response: {response.text if response else 'No response'}", flush=True)
        return {"status": "error", "message": "Order failed"}

    data = response.json()
    deal_ref = data.get("dealReference")

    print(f"[TRADE] {direction.upper()} {epic} @ {entry_price} (size {size}) → SUCCESS", flush=True)
    print(f"[TRADE] dealReference: {deal_ref}", flush=True)

    # -----------------------------------------
    # 4. LOG OPEN TRADE (NO DEALID)
    # -----------------------------------------
    ts = timestamp()

    log_open_trade(
        ticker=epic,
        epic=epic,
        deal_id=None,           # old system did NOT rely on dealId
        side=direction,
        size=size,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
        timeframe=timeframe,
        timestamp=ts,
    )

    session.update_last_trade()

    return {
        "status": "ok",
        "dealReference": deal_ref,
        "price": entry_price,
    }
