# ============================
# ORDER MODULE (FINAL — SAFE SL/TP + CONSISTENT LOGGING)
# ============================

import session
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_trade


def clamp_price(value):
    """
    Capital.com requires correct decimal precision.
    All US stocks in your 30‑ticker universe use 2 decimals.
    """
    try:
        return round(float(value), 2)
    except:
        return None


def validate_and_correct_levels(direction, midpoint, sl, tp):
    """
    Ensures SL/TP are valid for Capital.com and logically correct.
    Automatically fixes inverted SL/TP if Pine ever sends them.
    """

    sl_c = clamp_price(sl) if sl is not None else None
    tp_c = clamp_price(tp) if tp is not None else None

    if sl_c is None or tp_c is None:
        return None, None

    # BUY: SL < midpoint < TP
    if direction.lower() == "buy":
        if sl_c >= midpoint:
            sl_c = midpoint * 0.99  # push below market
        if tp_c <= midpoint:
            tp_c = midpoint * 1.01  # push above market

    # SELL: SL > midpoint > TP
    if direction.lower() == "sell":
        if sl_c <= midpoint:
            sl_c = midpoint * 1.01  # push above market
        if tp_c >= midpoint:
            tp_c = midpoint * 0.99  # push below market

    return clamp_price(sl_c), clamp_price(tp_c)


def place_order(epic, direction, size, sl=None, tp=None):
    """
    Places a BUY or SELL order with safe SL/TP correction.
    Fully compatible with parser, webhook, sizing, dashboard, and trade_log.
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

    midpoint = (bid + offer) / 2

    # ---------------------------------------------------------
    # CORRECT SL/TP BEFORE SENDING ORDER
    # ---------------------------------------------------------
    sl_fixed, tp_fixed = validate_and_correct_levels(direction, midpoint, sl, tp)

    print(f"[ORDER] Corrected SL: {sl_fixed}")
    print(f"[ORDER] Corrected TP: {tp_fixed}")

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

    if sl_fixed is not None:
        payload["stopLevel"] = sl_fixed

    if tp_fixed is not None:
        payload["profitLevel"] = tp_fixed

    print("[ORDER] Sending payload:", payload, flush=True)

    # ---------------------------------------------------------
    # SEND ORDER
    # ---------------------------------------------------------
    response = session.request("POST", API_POSITIONS, json=payload)

    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})", flush=True)
        print(f"[ERROR] Response: {response.text if response else 'No response'}", flush=True)
        return {"status": "error", "message": "Order failed"}

    try:
        data = response.json()
        deal_ref = data.get("dealReference")

        print(f"[TRADE] {direction.upper()} {epic} @ {midpoint} (size {size}) → SUCCESS", flush=True)
        print(f"[TRADE] dealReference: {deal_ref}", flush=True)

        # ---------------------------------------------------------
        # LOG TRADE (symbol/epic-consistent)
        # ---------------------------------------------------------
        log_trade(
            ticker=epic,              # using epic as ticker label in log
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
