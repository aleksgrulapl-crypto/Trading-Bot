# ============================
# SESSION MODULE (Debug Version)
# ============================

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

# ---------------------------------------------------------
# AUTHENTICATED REQUEST WRAPPER (uses auth.request for debug)
# ---------------------------------------------------------

def request(method, url, **kwargs):
    return auth.request(method, url, **kwargs)


# ---------------------------------------------------------
# ACCOUNT FETCHING
# ---------------------------------------------------------

def get_account():
    global shared_state

    try:
        print("\n" + "="*60)
        print("[SESSION] Fetching account...")
        print("="*60)

        r = request("GET", API_ACCOUNTS)

        print("[SESSION] Account raw response:")
        print(r.text)
        print("="*60)

        raw = r.json() if r.status_code == 200 else {}

        account = utils.parse_account(raw)
        shared_state["account"] = account
        return account

    except Exception as e:
        print("[SESSION] Account fetch error:", e)
        return shared_state["account"]


# ---------------------------------------------------------
# POSITION FETCHING
# ---------------------------------------------------------

def fetch_positions_from(endpoint):
    try:
        print("\n" + "="*60)
        print(f"[SESSION] Fetching positions from: {endpoint}")
        print("="*60)

        r = request("GET", endpoint + "?includeProfitLoss=true")

        print("[SESSION] Raw positions response:")
        print(r.text)
        print("="*60)

        if r.status_code != 200:
            return []

        return r.json().get("positions", [])

    except Exception as e:
        print(f"[SESSION] Position fetch error ({endpoint}):", e)
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
        print("[SESSION] Position fetch error:", e)
        return shared_state["positions"]


# ---------------------------------------------------------
# EPIC LOOKUP (FULL DEBUG)
# ---------------------------------------------------------

def verify_epic(ticker):
    print("\n" + "="*60)
    print(f"[EPIC LOOKUP] Looking up ticker: {ticker}")
    url = f"{API_MARKET}?search={ticker}"
    print(f"[EPIC LOOKUP] URL: {url}")
    print("="*60)

    try:
        r = request("GET", url)

        print("[EPIC LOOKUP] Status:", r.status_code)
        print("[EPIC LOOKUP] Raw response:")
        print(r.text)
        print("="*60)

        data = r.json()

        markets = data.get("markets", [])
        print("[EPIC LOOKUP] Parsed markets:")
        print(markets)
        print("="*60)

        if not markets:
            print("[EPIC LOOKUP] No markets found for ticker.")
            return {}

        print("[EPIC LOOKUP] Returning first market entry.")
        return markets[0]

    except Exception as e:
        print("[EPIC LOOKUP] ERROR:", e)
        return {}


# ---------------------------------------------------------
# POSITION ENRICHMENT
# ---------------------------------------------------------

def enrich_position(p):
    if not p:
        return p

    p["profitLoss"] = utils.calculate_profit_loss(
        direction=p.get("direction"),
        open_price=p.get("price"),
        current_price=p.get("current_price"),
        size=p.get("size")
    )

    return p


def enrich_positions(raw_positions):
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
