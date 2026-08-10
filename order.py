from auth import auth
import session
from trade_log import log_trade
import utils

def place_order(symbol, action, size, sl=None, tp=None):

    auth.ensure_token()   # SAFE

    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("instrument", {}).get("epic")

    if not epic:
        return {"status": "error", "message": "EPIC not found"}

    direction = "BUY" if action.lower() == "long" else "SELL"

    payload = {
        "epic": epic,
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "limitLevel": tp,
        "stopLevel": sl
    }

    r = session.request("POST", session.API_POSITIONS, json=payload)
    result = r.json()

    if "dealReference" in result:
        price = epic_data["snapshot"]["offer"]

        # FIX: log_trade() does NOT accept timestamp= argument
        log_trade(
    ticker=symbol,
    side=direction,
    size=size,
    price=price
)

        session.update_last_trade()

    return result
