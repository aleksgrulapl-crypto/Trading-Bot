# ============================
# ORDER MODULE (RESTORED + MODERNISED)
# ============================

import session
from auth import auth
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_trade

def place_order(epic, direction, size, sl=None, tp=None):
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print(f"[ERROR] Failed to fetch market snapshot for {epic}")
        return {"status": "error", "message": "Market snapshot unavailable"}

    snapshot = market.json().get("snapshot", {})
    price = snapshot.get("offer") if direction.upper() == "BUY" else snapshot.get("bid")

    if price is None:
        print(f"[ERROR] Market price unavailable for {epic}")
        return {"status": "error", "message": "Price unavailable"}

    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": float(size),
        "orderType": "MARKET",
        "level": None,
        "guaranteedStop": False
    }

    if sl is not None:
        payload["stopLevel"] = float(sl)

    if tp is not None:
        payload["limitLevel"] = float(tp)

    response = session.request("POST", API_POSITIONS, json=payload)

    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})")
        return {"status": "error", "message": "Order failed"}

    try:
        data = response.json()
        deal_ref = data.get("dealReference")

        print(f"[TRADE] {direction.upper()} {epic} @ {price} (size {size}) → SUCCESS")
        print(f"[TRADE] dealReference: {deal_ref}")

        log_trade(
            ticker=epic,
            side=direction.upper(),
            size=size,
            price=price,
            timestamp=timestamp()
        )

        session.update_last_trade()

        return {
            "status": "ok",
            "dealReference": deal_ref,
            "price": price
        }

    except Exception as e:
        print(f"[ERROR] Failed to parse order response: {e}")
        return {"status": "error", "message": "Order response parse error"}
