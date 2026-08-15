# ============================
# ORDER MODULE (FINAL — FIXED LOGGING + SAFE SL/TP)
# ============================

import session
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_trade


def clamp_price(value):
    try:
        return round(float(value), 2)
    except:
        return None


def validate_and_correct_levels(direction, midpoint, sl, tp):
    sl_c = clamp_price(sl) if sl is not None else None
    tp_c = clamp_price(tp) if tp is not None else None

    if sl_c is None or tp_c is None:
        return None, None

    if direction.lower() == "buy":
        if sl_c >= midpoint:
            sl_c = midpoint * 0.99
        if tp_c <= midpoint:
            tp_c = midpoint * 1.01

    if direction.lower() == "sell":
        if sl_c <= midpoint:
            sl_c = midpoint * 1.01
        if tp_c >= midpoint:
            tp_c = midpoint * 0.99

    return clamp_price(sl_c), clamp_price(tp_c)


def place_order(epic, direction, size, sl=None, tp=None):
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        return {"status": "error", "message": "Market snapshot unavailable"}

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        return {"status": "error", "message": "Price unavailable"}

    midpoint = (bid + offer) / 2

    sl_fixed, tp_fixed = validate_and_correct_levels(direction, midpoint, sl, tp)

    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": float(size),
        "orderType": "MARKET",
        "level": None,
        "guaranteedStop": False
    }

    if sl_fixed is not None:
        payload["stopLevel"] = sl_fixed
    if tp_fixed is not None:
        payload["profitLevel"] = tp_fixed

    response = session.request("POST", API_POSITIONS, json=payload)

    if not response or response.status_code >= 400:
        return {"status": "error", "message": "Order failed"}

    data = response.json()
    deal_ref = data.get("dealReference")

    # FIXED LOGGING — correct signature
    log_trade(
        ticker=epic,
        epic=epic,
        deal_id=deal_ref,
        side=direction.upper(),
        size=size,
        price=midpoint,
        sl=sl_fixed,
        tp=tp_fixed,
        timestamp=timestamp(),
        timeframe=None
    )

    session.update_last_trade()

    return {
        "status": "ok",
        "dealReference": deal_ref,
        "price": midpoint
    }

    except Exception as e:
        print(f"[ERROR] Failed to parse order response: {e}", flush=True)
        return {"status": "error", "message": "Order response parse error"}
