# ============================
# SESSION MODULE (REVERTED — OLD LOGIC, SIMPLE & STABLE)
# ============================

import time
import pprint
from auth import auth
from config import API_POSITIONS, API_ACCOUNT, API_MARKET
from utils import timestamp


# ---------------------------------------------------------
# SHARED STATE (unchanged)
# ---------------------------------------------------------

shared_state = {
    "account": {},
    "positions": [],
    "trade_log": [],
    "system_status": {
        "last_webhook": None,
        "last_trade": None,
        "auth": "OK"
    },
    "daily_report": {}
}

_cache = {
    "account": {"ts": 0, "data": {}}
}


# ---------------------------------------------------------
# HEADERS
# ---------------------------------------------------------

def get_headers():
    auth.ensure_token()
    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
    }


# ---------------------------------------------------------
# REQUEST WRAPPER
# ---------------------------------------------------------

def request(method, url, json=None):
    headers = get_headers()

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        if response.status_code >= 400:
            print(f"[ERROR] {method} {url} → {response.status_code}", flush=True)
            print(f"[ERROR] Response: {response.text}", flush=True)

        return response

    except Exception as e:
        print(f"[ERROR] Request failed: {e}", flush=True)
        return None


# ---------------------------------------------------------
# GET POSITIONS (RAW)
# ---------------------------------------------------------

def get_positions():
    """
    Reverted: return raw positions exactly as Capital.com provides them.
    No enrichment, no transformation.
    """
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        return []

    try:
        data = response.json()
        positions = data.get("positions", [])

        if positions:
            print("\n[DEBUG] RAW POSITION SAMPLE:")
            pprint.pprint(positions[0], width=200)
            print("\n")

        return positions

    except Exception as e:
        print(f"[SESSION] Failed to parse positions: {e}", flush=True)
        return []


# ---------------------------------------------------------
# PARSE POSITIONS (OLD LOGIC)
# ---------------------------------------------------------

def parse_positions(raw_positions):
    """
    Reverted: simple, stable parsing.
    Extract only the fields needed for closing + dashboard.
    """
    parsed = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        parsed.append({
            "id": pos.get("dealId"),          # OLD SYSTEM USED THIS DIRECTLY
            "ticker": market.get("symbol"),
            "epic": market.get("epic"),
            "direction": pos.get("direction"),
            "size": pos.get("size"),
            "entry_price": pos.get("level"),
            "current_price": (
                market.get("bid") if pos.get("direction") == "SELL"
                else market.get("offer")
            ),
            "pnl": pos.get("upl", 0),
            "currency": pos.get("currency"),
        })

    return parsed


# ---------------------------------------------------------
# ENRICH POSITIONS (REVERTED)
# ---------------------------------------------------------

def enrich_positions(raw_positions):
    """
    Reverted: simply call parse_positions.
    No signature, no merging, no analytics fields.
    """
    return parse_positions(raw_positions)


# ---------------------------------------------------------
# GET ACCOUNT
# ---------------------------------------------------------

def get_account():
    now = time.time()
    if now - _cache["account"]["ts"] < 1.0:
        return _cache["account"]["data"]

    response = request("GET", API_ACCOUNT)

    if not response or response.status_code != 200:
        return _cache["account"]["data"]

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        acc = accounts[0] if accounts else {}

        _cache["account"]["ts"] = now
        _cache["account"]["data"] = acc
        return acc

    except Exception as e:
        print(f"[SESSION] Failed to parse account: {e}", flush=True)
        return _cache["account"]["data"]


# ---------------------------------------------------------
# ENRICH ACCOUNT (unchanged)
# ---------------------------------------------------------

def enrich_account(raw):
    if not raw:
        return {}

    bal = raw.get("balance", {})

    available = bal.get("available", 0)
    margin_warning = None

    if available < 0:
        margin_warning = "⚠ Margin Warning: Available balance is negative."

    return {
        "funds": round(bal.get("funds", 0), 2),
        "balance": round(bal.get("balance", 0), 2),
        "pnl": round(bal.get("profitLoss", 0), 2),
        "margin": round(bal.get("profitLoss", 0), 2),
        "available": round(available, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": margin_warning
    }


# ---------------------------------------------------------
# UPDATE LAST TRADE / WEBHOOK
# ---------------------------------------------------------

def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
