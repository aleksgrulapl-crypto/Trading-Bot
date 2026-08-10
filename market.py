from auth import auth
from config import API_MARKET, CAPITAL_API_KEY
import requests

# ---------------------------------------------------------
# AUTHENTICATED REQUEST WRAPPER
# ---------------------------------------------------------

def request(method, url, **kwargs):
    auth.ensure_token()

    headers = kwargs.pop("headers", {})
    headers.update({
        "X-CAP-API-KEY": CAPITAL_API_KEY,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
    })

    return auth.session.request(method, url, headers=headers, **kwargs)


# ---------------------------------------------------------
# INSTRUMENT LOOKUP
# ---------------------------------------------------------

def get_instrument(epic):
    """
    Fetch instrument metadata from Capital.com.
    Returns:
        {
            "epic": "...",
            "instrumentType": "...",
            "symbol": "...",
            "marketStatus": "...",
            "bid": ...,
            "offer": ...,
            ...
        }
    """
    if not epic:
        return {}

    try:
        r = request("GET", f"{API_MARKET}/{epic}")
        if r.status_code != 200:
            return {}

        return r.json()

    except Exception as e:
        print("Instrument lookup error:", e)
        return {}
