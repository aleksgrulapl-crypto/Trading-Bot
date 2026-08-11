# ============================
# SESSION MODULE (FINAL VERSION)
# ============================

from auth import auth
from config import (
    API_ACCOUNTS,
    API_POSITIONS,
    API_BASE
)
import utils
from trade_log import load_log

shared_state = {
    "account": {},
    "positions": [],
    "trade_log": [],
    "daily_report": {},
    "system_status": {
        "last_webhook": None,
        "last_trade": None,
        "auth": "OK"
    }
}

# ---------------------------------------------------------
# AUTHENTICATED REQUEST WRAPPER
# ---------------------------------------------------------

def request(method, url, **kwargs):
    return auth.request(method, url, **kwargs)

# ---------------------------------------------------------
# DIRECT EPIC LOOKUP (correct + stable)
# ---------------------------------------------------------

def verify_epic(ticker):
    print("\n" + "="*60)
    print(f"[EPIC LOOKUP] Ticker: {ticker}")

    epic = utils.resolve_epic_from_ticker(ticker)
    print(f"[EPIC LOOKUP] Resolved EPIC: {epic}")

    if not epic:
        print("[EPIC LOOKUP] No EPIC mapping found.")
        return {}

    url = f"{API_BASE}/api/v1/markets/{epic}"
    print(f"[EPIC LOOKUP] URL: {url}")
    print("="*60)

    try:
        r = request("GET", url)

        print("[EPIC LOOKUP] Status:", r.status_code)
        print("[EPIC LOOKUP] Raw response:")
        print(r.text)
        print("="*60)

        if r.status_code != 200:
            print("[EPIC LOOKUP] Failed.")
            return {}

        return r.json()

    except Exception as e:
        print("[EPIC LOOKUP] ERROR:", e)
        return {}

# ---------------------------------------------------------
# ACCOUNT FETCHING
# ---------------------------------------------------------

def get_account():
    try:
        r = request("GET", API_ACCOUNTS)
        raw = r.json() if r.status_code == 200 else {}
        account = utils.parse_account(raw)
        shared_state["account"] = account
        return account
    except Exception as e:
        print("Account fetch error:", e)
        return shared_state["account"]

# ---------------------------------------------------------
# ACCOUNT ENRICHMENT (dashboard compatibility)
# ---------------------------------------------------------

def enrich_account(raw_account):
    # Dashboard expects this function to exist
    return raw_account

# ---------------------------------------------------------
# POSITIONS FETCHING
# ---------------------------------------------------------

def get_positions():
    try:
        r = request("GET", API_POSITIONS + "?includeProfitLoss=true")
        raw = r.json().get("positions", []) if r.status_code == 200 else []
        parsed = utils.parse_positions(raw)
        shared_state["positions"] = parsed
        return parsed
    except Exception as e:
        print("Position fetch error:", e)
        return shared_state["positions"]

# ---------------------------------------------------------
# POSITION ENRICHMENT (dashboard compatibility)
# ---------------------------------------------------------

def enrich_positions(raw_positions):
    enriched = []
    for p in raw_positions:
        p["profitLoss"] = utils.calculate_profit_loss(
            p.get("direction"),
            p.get("price"),
            p.get("current_price"),
            p.get("size")
        )
        enriched.append(p)
    return enriched

# ---------------------------------------------------------
# DAILY REPORT (dashboard compatibility)
# ---------------------------------------------------------

def get_daily_report():
    return shared_state.get("daily_report", {})

# ---------------------------------------------------------
# TRADE LOG
# ---------------------------------------------------------

def refresh_trade_log():
    shared_state["trade_log"] = load_log()
    return shared_state["trade_log"]

# ---------------------------------------------------------
# SYSTEM STATUS UPDATES
# ---------------------------------------------------------

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = utils.timestamp()

def update_last_trade():
    shared_state["system_status"]["last_trade"] = utils.timestamp()
