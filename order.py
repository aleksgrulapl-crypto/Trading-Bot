# ============================
# ORDER MODULE (FINAL CLEAN)
# ============================

import session
from auth import auth
from config import API_POSITIONS
from utils import timestamp

# ---------------------------------------------------------
# PLACE ORDER
# ---------------------------------------------------------

def place_order(epic, direction, size, sl=None, tp=None):
    """
    Places a BUY or SELL order with optional SL/TP.
    """

    # Determine correct price reference
    # BUY uses offer, SELL uses bid
    market = session.request("GET", f"https://api-capital.backend-capital.com/api/v1/markets/{epic}")
    if not market or market.status_code != 200:
        print(f"[ERROR] Failed to fetch market snapshot for {epic}")
        return None

    snapshot = market.json().get("snapshot", {})
    price = snapshot.get("offer") if direction.upper() == "BUY" else snapshot.get("bid")

    if price is None:
        print(f"[ERROR] Market price unavailable for {epic}")
        return None

    # Build order payload
    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": float(size),
        "orderType": "MARKET",
        "level": None,
        "guaranteedStop": False
    }

    # Stop Loss
    if sl is not None:
        payload["stopLevel"] = float(sl)

    # Take Profit
    if tp is not None:
        payload["limitLevel"] = float(tp)

    # Send order
    response = session.request("POST", API_POSITIONS, json=payload)

    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})")
        return None

    try:
        data = response.json()
        deal_ref = data.get("dealReference")

        print(f"[TRADE] {direction.upper()} {epic} @ {price} (size {size}) → SUCCESS")
        print(f"[TRADE] dealReference: {deal_ref}")

        # Update dashboard system status
        session.update_last_trade()

        return deal_ref

    except Exception as e:
        print(f"[ERROR] Failed to parse order response: {e}")
        return None
