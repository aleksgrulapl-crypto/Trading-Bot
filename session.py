from auth import auth
from config import (
    CAPITAL_API_KEY,
    API_ACCOUNTS,
    API_POSITIONS,
    API_MARKET
)
import utils
from trade_log import load_log
import requests

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
# ACCOUNT FETCHING (CFD ONLY — STOCK ACCOUNT REMOVED)
# ---------------------------------------------------------

def get_account():
    global shared_state

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
# POSITION FETCHING + METADATA ENRICHMENT
# ---------------------------------------------------------

def fetch_positions_from(endpoint):
    try:
        r = request("GET", endpoint + "?includeProfitLoss=true")
        if r.status_code != 200:
            return []
        return r.json().get("positions", [])
    except Exception as e:
        print(f"Position fetch error ({endpoint}):", e)
        return []

def enrich_position(p):
    """Attach instrument metadata + ticker + profitLoss."""
    pos = p.get("position", {})
    epic = pos.get("epic")

    if epic:
        meta = verify_epic(epic)
        p["instrument"] = meta
        p["profitLoss"] = meta.get("profitLoss", p.get("profitLoss"))
    else:
        p["instrument"] = {}
        p["profitLoss"] = p.get("profitLoss")

    return p

def get_positions():
    global shared_state

    try:
        all_positions = []

        all_positions += fetch_positions_from(API_POSITIONS)
        all_positions += fetch_positions_from(API_POSITIONS + "/otc")
        all_positions += fetch_positions_from(API_POSITIONS + "/otc/open")
        all_positions += fetch_positions_from(API_POSITIONS + "/spot")
        all_positions += fetch_positions_from(API_POSITIONS + "/crypto")

        enriched = [enrich_position(p) for p in all_positions]
        parsed = utils.parse_positions(enriched)

        shared_state["positions"] = parsed
        return parsed

    except Exception as e:
        print("Position fetch error:", e)
        return shared_state["positions"]

# ---------------------------------------------------------
# EPIC LOOKUP
# ---------------------------------------------------------

def verify_epic(symbol):
    try:
        r = request("GET", f"{API_MARKET}/{symbol}")
        return r.json()
    except Exception as e:
        print("EPIC lookup error:", e)
        return {}

# ---------------------------------------------------------
# TRADE LOG + REPORT
# ---------------------------------------------------------

def refresh_trade_log():
    shared_state["trade_log"] = load_log()
    return shared_state["trade_log"]

def set_daily_report(report):
    shared_state["daily_report"] = report

def get_daily_report():
    return shared_state.get("daily_report", {})

# ---------------------------------------------------------
# SYSTEM STATUS
# ---------------------------------------------------------

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = utils.timestamp()

def update_last_trade():
    shared_state["system_status"]["last_trade"] = utils.timestamp()
