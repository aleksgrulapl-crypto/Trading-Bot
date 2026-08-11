from auth import auth
import session
from trade_log import log_trade
import utils
from config import API_POSITIONS

def place_order(ticker, action, size, sl=None, tp=None):

    # Ensure CST/XST are valid
    auth.ensure_token()

    # EPIC lookup (must return instrument.epic)
    epic_data = session.verify_epic(ticker)

    epic = epic_data.get("instrument", {}).get("epic")
    if not epic:
        return {
            "status": "error",
            "message": f"EPIC not found for ticker {ticker}",
            "epic_data": epic_data
        }

    # BUY / SELL
    direction = "BUY" if action.lower() == "buy" else "SELL"

    # Build order payload
    payload = {
        "epic": epic,
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "guaranteedStop": False
    }

    # Optional SL/TP
    if tp:
        payload["limitLevel"] = tp
    if sl:
        payload["stopLevel"] = sl

    print("\n" + "="*60)
    print("[ORDER] Sending order payload:")
    print(payload)
    print("="*60)

    # Send order
    r = session.request("POST", API_POSITIONS, json=payload)
    result = r.json()

    print("[ORDER] Raw response:")
    print(result)
    print("="*60)

    # If trade executed → log it
    if "dealReference" in result:
        price = epic_data.get("snapshot", {}).get("offer")

        log_trade(
            ticker=ticker,
            side=direction,
            size=size,
            price=price,
            pnl=None,  # pnl is calculated later by dashboard
            timestamp=utils.timestamp()
        )

        session.update_last_trade()

    return result
