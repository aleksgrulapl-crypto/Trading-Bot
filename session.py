# ============================
# SESSION MODULE (FINAL RESTORED + UPDATED)
# ============================

import requests
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET, EPIC_MAP
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
# RAW FETCH (USED BY /raw ENDPOINT)
# ---------------------------------------------------------

def fetch_positions_from(url):
    response = request("GET", url)
    if not response or response.status_code != 200:
        return {}
    try:
        return response.json()
    except Exception:
        return {}

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
        positions = data.get("positions", [])
        shared_state["positions"] = positions
        return positions
    except Exception:
        return []

# ---------------------------------------------------------
# GET ACCOUNT (USES API_ACCOUNTS)
# ---------------------------------------------------------

def get_account():
    """
    Returns correct Capital.com account structure:
    - balance.balance = cash
    - balance.profitLoss = running PnL
    - balance.available = equity - margin
    """
    response = request("GET", API_ACCOUNTS)
    if not response or response.status_code != 200:
        return {}

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        account = accounts[0] if accounts else {}
        shared_state["account"] = account
        return account
    except Exception:
        return {}

# ---------------------------------------------------------
# ENRICH ACCOUNT
# ---------------------------------------------------------

def enrich_account(raw):
    if not raw:
        return {}

    bal = raw.get("balance", {})

    cash = bal.get("balance", 0)
    pnl = bal.get("profitLoss", 0)
    available = bal.get("available", 0)

    equity = cash + pnl

    margin_warning = None
    if available < 0:
        margin_warning = "⚠ Margin Warning: Available balance is negative."

    return {
        "balance": round(cash, 2),
        "equity": round(equity, 2),
        "margin": round(pnl, 2),
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
# ENRICH POSITIONS
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
            "currency": pos.get("currency")
        })

    return enriched

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
