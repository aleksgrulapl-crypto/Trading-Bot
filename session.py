# ============================
# SESSION MODULE (Corrected)
# ============================

from auth import auth
from config import (
    CAPITAL_API_KEY,
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
# DIRECT EPIC LOOKUP (old bot logic restored)
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
# POSITIONS
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
# TRADE LOG + REPORT
# ---------------------------------------------------------

def refresh_trade_log():
    shared_state["trade_log"] = load_log()
    return shared_state["trade_log"]

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = utils.timestamp()

def update_last_trade():
    shared_state["system_status"]["last_trade"] = utils.timestamp()
