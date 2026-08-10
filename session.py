from auth import auth
from config import (
    CAPITAL_API_KEY,
    API_ACCOUNTS,
    API_POSITIONS,
    API_MARKET
)
import utils
import market
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
# ACCOUNT FETCHING + ENRICHMENT
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


def enrich_account(raw_account):
    """Enrich account data with balance, equity, margin."""
    if not raw_account:
        return raw_account

    return {
        "balance": raw_account.get("balance"),
        "equity": raw_account.get("equity"),
        "margin": raw_account.get("margin")
    }


# ---------------------------------------------------------
# POSITION FETCHING (RAW)
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


def get_positions():
    global shared_state

    try:
        all_positions = []

        all_positions += fetch_positions_from(API_POSITIONS)
        all_positions += fetch_positions_from(API_POSITIONS + "/otc")
        all_positions += fetch_positions_from(API_POSITIONS + "/otc/open")
        all_positions += fetch_positions_from(API_POSITIONS + "/spot")
        all_positions += fetch_positions_from(API_POSITIONS + "/crypto")

        parsed = utils.parse_positions(all_positions)
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
# POSITION ENRICHMENT PIPELINE
# ---------------------------------------------------------

def enrich_position(p):
    if not p:
        return p

    # ticker already parsed from market.symbol
    epic = p.get("epic")

    # instrument metadata already included
    instrument = p.get("instrument", {})

    # calculate profit/loss using correct fields
    p["profitLoss"] = utils.calculate_profit_loss(
        direction=p.get("direction"),
        open_price=p.get("price"),
        current_price=p.get("current_price"),
        size=p.get("size")
    )

    return p



def enrich_positions(raw_positions):
    """Enrich all positions returned by the API."""
    enriched = []
    for p in raw_positions:
        enriched.append(enrich_position(p))
    return enriched


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
