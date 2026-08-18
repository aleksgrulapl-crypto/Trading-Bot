# ============================
# SESSION MODULE (CORRECTED + DEALREFERENCE FIX)
# ============================

import requests
from auth import auth
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report

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

# Simple cache for account refresh throttling
_cache = {
    "account": {"ts": 0, "data": {}}
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
    except Exception as e:
        print(f"[SESSION] Failed to parse positions: {e}", flush=True)
        return []


# ---------------------------------------------------------
# GET ACCOUNT
# ---------------------------------------------------------

def get_account():
    # throttle refresh
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
# ENRICH POSITIONS (CRITICAL FIX APPLIED)
# ---------------------------------------------------------

def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        # CRITICAL FIX:
        # Capital.com uses dealReference for closing positions.
        # dealId may be UUID or internal ID and NOT closable.
        position_id = pos.get("dealReference") or pos.get("dealId")

        profit = pos.get("upl", 0)

        enriched.append({
            "id": position_id,                         # <-- FIXED
            "ticker": market.get("symbol"),
            "epic": market.get("epic"),

            "size": pos.get("size"),
            "price": pos.get("level"),

            "current_price": (
                market.get("bid") if pos.get("direction") == "SELL"
                else market.get("offer")
            ),

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
        "funds": round(bal.get("funds", 0), 2),
        "balance": round(bal.get("balance", 0), 2),
        "pnl": round(bal.get("profitLoss", 0), 2),
        "margin": round(bal.get("profitLoss", 0), 2),
        "available": round(available, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": margin_warning
    }


# ---------------------------------------------------------
# EPIC LOOKUP (RESTORED)
# ---------------------------------------------------------

def verify_epic(symbol):
    symbol = symbol.upper()

    # 1. Local mapping
    if symbol in EPIC_MAP:
        return {"epic": EPIC_MAP[symbol], "source": "map"}

    # 2. API lookup
    try:
        url = f"{API_MARKET}/{symbol}"
        r = request("GET", url)

        if not r or r.status_code != 200:
            print(f"[EPIC] API lookup failed for {symbol}", flush=True)
            return {"epic": None, "source": "api_error"}

        data = r.json()
        epic = data.get("instrument", {}).get("epic")

        if epic:
            return {"epic": epic, "source": "api"}

        print(f"[EPIC] No EPIC found for {symbol}", flush=True)
        return {"epic": None, "source": "not_found"}

    except Exception as e:
        print(f"[EPIC] Exception during lookup: {e}", flush=True)
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
        print(f"[REPORT] Failed to load daily report: {e}", flush=True)
        return {}


# ---------------------------------------------------------
# UPDATE LAST TRADE
# ---------------------------------------------------------

def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()


# ---------------------------------------------------------
# UPDATE LAST WEBHOOK
# ---------------------------------------------------------

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
