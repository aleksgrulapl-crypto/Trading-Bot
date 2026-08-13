# ============================
# ORDER MODULE (RESTORED + UPDATED + FIXED)
# ============================

import session
from auth import auth
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_trade

def place_order(epic, direction, size, sl=None, tp=None):
    """
    Places a BUY or SELL order with optional SL/TP.
    Fully compatible with restored parser, sizing, session, and webhook.
    """

    # ---------------------------------------------------------
    # FETCH MARKET SNAPSHOT
    # ---------------------------------------------------------
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print(f"[ERROR] Failed to fetch market snapshot for {epic}", flush=True)
        return {"status": "error", "message": "Market snapshot unavailable"}

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print(f"[ERROR] Market prices unavailable for {epic}", flush=True)
        return {"status": "error", "message": "Price unavailable"}

    # Midpoint price (consistent with sizing)
    midpoint = (bid + offer) / 2

    # ---------------------------------------------------------
    # BUILD ORDER PAYLOAD
    # ---------------------------------------------------------
    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": float(size),
        "orderType": "MARKET",
        "level": None,
        "guaranteedStop": False
    }

    # STOP LOSS
    if sl is not None:
        payload["stopLevel"] = float(sl)

    # TAKE PROFIT
    if tp is not None:
        payload["profitLevel"] = float(tp)

    print("[ORDER] Sending payload:", payload, flush=True)

    # ---------------------------------------------------------
    # SEND ORDER
    # ---------------------------------------------------------
    response = session.request("POST", API_POSITIONS, json=payload)

    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})", flush=True)
        print(f"[ERROR] Response: {response.text}", flush=True)
        return {"status": "error", "message": "Order failed"}

    try:
        data = response.json()
        deal_ref = data.get("dealReference")

        print(f"[TRADE] {direction.upper()} {epic} @ {midpoint} (size {size}) → SUCCESS", flush=True)
        print(f"[TRADE] dealReference: {deal_ref}", flush=True)

        # ---------------------------------------------------------
        # LOG TRADE (FIXED)
        # ---------------------------------------------------------
        log_trade(
            ticker=epic,            # epic is correct for Capital.com
            side=direction.upper(),
            size=size,
            price=midpoint,
            sl=sl,
            tp=tp,
            timestamp=timestamp()
        )

        # Update dashboard system status
        session.update_last_trade()

        return {
            "status": "ok",
            "dealReference": deal_ref,
            "price": midpoint
        }

    except Exception as e:
        print(f"[ERROR] Failed to parse order response: {e}", flush=True)
        return {"status": "error", "message": "Order response parse error"}
