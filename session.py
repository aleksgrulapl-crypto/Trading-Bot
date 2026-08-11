# ============================
# SESSION MODULE (FINAL CLEAN)
# ============================

import requests
from auth import auth
from config import API_POSITIONS, API_ACCOUNT
from utils import timestamp

# Shared state for dashboard
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

# ---------------------------------------------------------
# GET HEADERS
# ---------------------------------------------------------

def get_headers():
    auth.ensure_token()
    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
    }

# ---------------------------------------------------------
# REQUEST WRAPPER (CLEAN LOGGING)
# ---------------------------------------------------------

def request(method, url, json=None):
    headers = get_headers()

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        # Only log errors
        if response.status_code >= 400:
            print(f"[ERROR] {method} {url} → {response.status_code}")
            print(f"[ERROR] Response: {response.text}")

        return response

    except Exception as e:
        print(f"[ERROR] Request failed: {e}")
        return None

# ---------------------------------------------------------
# GET POSITIONS (RAW)
# ---------------------------------------------------------

def get_positions():
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        return []

    try:
        data = response.json()
        return data.get("positions", [])
    except:
        return []

# ---------------------------------------------------------
# GET ACCOUNT (RAW)
# ---------------------------------------------------------

def get_account():
    response = request("GET", API_ACCOUNT)

    if not response or response.status_code != 200:
        return {}

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        return accounts[0] if accounts else {}
    except:
        return {}

# ---------------------------------------------------------
# ENRICH POSITIONS (CORRECT PnL LOGIC)
# ---------------------------------------------------------

def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item["position"]
        market = item["market"]

        # Correct PnL: use UPL (unrealised PnL)
        profit = pos.get("upl", 0)

        enriched.append({
            "id": pos.get("dealId"),
            "ticker": market.get("symbol"),
            "epic": market.get("epic"),
            "size": pos.get("size"),
            "price": pos.get("level"),
            "current_price": market.get("bid") if pos.get("direction") == "SELL" else market.get("offer"),
            "direction": pos.get("direction"),
            "profit": round(profit, 2),
            "stopLevel": pos.get("stopLevel"),
            "limitLevel": pos.get("profitLevel"),
            "currency": pos.get("currency")
        })

    return enriched

# ---------------------------------------------------------
# ENRICH ACCOUNT
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
        "balance": round(bal.get("balance", 0), 2),
        "equity": round(bal.get("balance", 0) + bal.get("profitLoss", 0), 2),
        "margin": round(bal.get("profitLoss", 0), 2),
        "available": round(available, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": margin_warning
    }

# ---------------------------------------------------------
# UPDATE LAST TRADE TIMESTAMP
# ---------------------------------------------------------

def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()

# ---------------------------------------------------------
# UPDATE LAST WEBHOOK TIMESTAMP
# ---------------------------------------------------------

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
