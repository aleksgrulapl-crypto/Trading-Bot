# ============================
# ORDER MODULE (CONFIRMS-BASED DEALID MAPPING + UNIFIED OPEN LOGGING)
# ============================

import time
import session
from auth import auth
from config import API_POSITIONS, API_MARKET
from utils import timestamp
from trade_log import log_open_trade


def place_order(epic, direction, size, sl=None, tp=None, timeframe=None):
    """
    Place a BUY/SELL market order and log an OPEN trade
    in unified format, with robust dealId mapping via confirms endpoint.
    """

    auth.ensure_token()

    # ---------------------------------------------------------
    # 1. MARKET SNAPSHOT
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 2. BUILD ORDER PAYLOAD
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 3. SEND ORDER
    # ---------------------------------------------------------
    response = session.request("POST", API_POSITIONS, json=payload)
    if not response or response.status_code >= 400:
        print(f"[ERROR] Order failed for {epic} ({direction})", flush=True)
        print(f"[ERROR] Response: {response.text if response else 'No response'}", flush=True)
        return {"status": "error", "message": "Order failed"}

    data = response.json()
    deal_ref = data.get("dealReference")

    print(f"[TRADE] {direction.upper()} {epic} @ {entry_price} (size {size}) → SUCCESS", flush=True)
    print(f"[TRADE] dealReference: {deal_ref}", flush=True)

    # ---------------------------------------------------------
    # 4. DEALID MAPPING VIA CONFIRMS ENDPOINT (CORRECT)
    # ---------------------------------------------------------
    real_deal_id = None

    # Small retry window in case confirm is not immediately ready
    for attempt in range(10):  # 10 × 0.2s = 2 seconds
        time.sleep(0.2)

        confirm = session.request("GET", f"{API_POSITIONS}/confirms/{deal_ref}")
        if not confirm:
            continue

        if confirm.status_code == 200:
            body = confirm.json()
            real_deal_id = body.get("dealId")
            if real_deal_id:
                break
        else:
            print(f"[ORDER] Confirm attempt {attempt+1} failed: {confirm.status_code} {confirm.text}", flush=True)

    if real_deal_id:
        print(f"[ORDER] Mapped dealReference → dealId via confirms: {real_deal_id}", flush=True)
    else:
        print("[WARN] Could not map dealReference → dealId via confirms. Logging OPEN trade with dealId=None.", flush=True)

    # ---------------------------------------------------------
    # 5. LOG OPEN TRADE (UNIFIED FORMAT)
    # ---------------------------------------------------------
    ts = timestamp()

    log_open_trade(
        ticker=epic,
        epic=epic,
        deal_id=real_deal_id,
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
        "dealId": real_deal_id,
        "price": entry_price,
    }
