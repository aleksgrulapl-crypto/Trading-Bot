# ============================
# SESSION MODULE (FINAL CLEAN + TIMEFRAME SUPPORT)
# ============================

import requests
from auth import auth
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report
from trade_log import load_log

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
            print(f"[ERROR] {method} {url} → {response.status_code}")
            print(f"[ERROR] Response: {response.text}")
        return response
    except Exception as e:
        print(f"[ERROR] Request failed: {e}")
        return None

# ---------------------------------------------------------
# GET POSITIONS
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
# GET ACCOUNT
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
# ENRICH POSITIONS (NOW INCLUDES TIMEFRAME)
# ---------------------------------------------------------

def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item["position"]
        market = item["market"]

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
            "currency": pos.get("currency"),

            # NEW: timeframe support (injected from trade log)
            "timeframe": extract_timeframe_from_log(market.get("symbol"))
        })

    return enriched

# ---------------------------------------------------------
# TIMEFRAME LOOKUP FROM TRADE LOG
# ---------------------------------------------------------

def extract_timeframe_from_log(ticker):
    """
    Finds the most recent trade for this ticker and returns its timeframe.
    Ensures closed trades inherit correct strategy timeframe.
    """
    log = load_log()
    for entry in reversed(log):
        if entry.get("ticker") == ticker and entry.get("timeframe"):
            return entry.get("timeframe")
    return None

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
# EPIC LOOKUP
# ---------------------------------------------------------

def verify_epic(symbol):
    symbol = symbol.upper()

    if symbol in EPIC_MAP:
        return {"epic": EPIC_MAP[symbol], "source": "local"}

    try:
        url = f"{API_MARKET}/{symbol}"
        r = request("GET", url)

        if not r or r.status_code != 200:
            print(f"[EPIC] API lookup failed for {symbol}")
            return {"epic": None, "source": "api_error"}

        data = r.json()
        epic = data.get("instrument", {}).get("epic")

        if epic:
            return {"epic": epic, "source": "api"}

        print(f"[EPIC] No EPIC found for {symbol}")
        return {"epic": None, "source": "not_found"}

    except Exception as e:
        print(f"[EPIC] Exception during lookup: {e}")
        return {"epic": None, "source": "exception"}

# ---------------------------------------------------------
# DAILY REPORT
# ---------------------------------------------------------

def get_daily_report():
    try:
        report_data = report.get_daily_report()
        shared_state["daily_report"] = report_data
        return report_data
    except Exception as e:
        print(f"[REPORT] Failed to load daily report: {e}")
        return {}

# ---------------------------------------------------------
# SYSTEM STATUS UPDATES
# ---------------------------------------------------------

def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
