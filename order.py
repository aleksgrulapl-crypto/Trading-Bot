# ============================
# ORDER MODULE (UNIFIED OPEN LOGGING, NO DEALID DEPENDENCY)
# ============================

import session
from auth import auth
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_open_trade


def place_order(epic, direction, size, sl=None, tp=None, timeframe=None):
    """
    Place a BUY/SELL market order and log an OPEN trade
    in unified format. No reliance on numeric dealId.
    """

    auth.ensure_token()

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

    if direction.lower() == "buy":
        entry_price = offer
    else:
        entry_price = bid

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

    response = session.request("POST", API_POSITIONS, json=payload)
    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})", flush=True)
        print(f"[ERROR] Response: {response.text if response else 'No response'}", flush=True)
        return {"status": "error", "message": "Order failed"}

    data = response.json()
    deal_ref = data.get("dealReference")

    print(f"[TRADE] {direction.upper()} {epic} @ {entry_price} (size {size}) → SUCCESS", flush=True)
    print(f"[TRADE] dealReference: {deal_ref}", flush=True)

    ts = timestamp()

    # We do NOT depend on numeric dealId; dashboard dedupe uses dealId if present,
    # but we keep this None to avoid fragile matching.
    log_open_trade(
        ticker=epic,
        epic=epic,
        deal_id=None,
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
