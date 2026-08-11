from auth import auth
import session
from trade_log import log_trade
import utils

def place_order(ticker, action, size, sl=None, tp=None):

    auth.ensure_token()   # SAFE

    # 1. Lookup instrument using the TICKER (NVDA, MU, TSLA)
    epic_data = session.verify_epic(ticker)
    epic = epic_data.get("instrument", {}).get("epic")

    if not epic:
        return {"status": "error", "message": f"EPIC not found for ticker {ticker}"}

    # 2. Correct direction mapping
    direction = "BUY" if action.lower() == "buy" else "SELL"

    payload = {
        "epic": epic,          # <-- REAL EPIC from Capital.com
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "limitLevel": tp,      # TP
        "stopLevel": sl        # SL
    }

    r = session.request("POST", session.API_POSITIONS, json=payload)
    result = r.json()

    if "dealReference" in result:
        price = epic_data["snapshot"]["offer"]

        log_trade(
            ticker=ticker,     # <-- Store original ticker
            side=direction,
            size=size,
            price=price
        )

        session.update_last_trade()

    return result
