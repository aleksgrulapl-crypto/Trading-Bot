from auth import auth
import session
from trade_log import log_trade
import utils
from config import API_POSITIONS

def place_order(ticker, action, size, sl=None, tp=None):

    auth.ensure_token()

    epic_data = session.verify_epic(ticker)

    epic = epic_data.get("instrument", {}).get("epic")
    if not epic:
        return {"status": "error", "message": f"EPIC not found for ticker {ticker}"}

    direction = "BUY" if action.lower() == "buy" else "SELL"

    payload = {
        "epic": epic,
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "limitLevel": tp,
        "stopLevel": sl
    }

    r = session.request("POST", API_POSITIONS, json=payload)
    result = r.json()

    if "dealReference" in result:
        price = epic_data.get("snapshot", {}).get("offer", None)

        log_trade(
            ticker=ticker,
            side=direction,
            size=size,
            price=price
        )

        session.update_last_trade()

    return result
