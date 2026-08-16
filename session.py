# ============================
# SESSION MODULE (STABLE + CACHED + UPDATED)
# ============================

import time
import requests
from math import isnan

from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET, EPIC_MAP
from utils import timestamp
import report

# ============================
# GLOBAL STATE
# ============================

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

# Cache storage
_cache = {
    "positions": {"data": None, "ts": 0},
    "account": {"data": None, "ts": 0},
    "request_cooldown": 0,
    "login_cooldown": 0
}

CACHE_SECONDS = 5
LOGIN_COOLDOWN_SECONDS = 30


# ============================
# CLEANER
# ============================

def clean_value(v):
    try:
        if v is None:
            return None
        if isinstance(v, float) and isnan(v):
            return None
        return v
    except:
        return None


def clean_structure(obj):
    if isinstance(obj, dict):
        return {k: clean_structure(clean_value(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_structure(clean_value(x)) for x in obj]
    return clean_value(obj)


# ============================
# AUTH + REQUEST
# ============================

def get_headers():
    # Prevent login spam
    now = time.time()
    if now < _cache["login_cooldown"]:
        print("[AUTH] Login cooldown active, skipping ensure_token", flush=True)
    else:
        try:
            auth.ensure_token()
            shared_state["system_status"]["auth"] = "OK"
        except Exception as e:
            print(f"[AUTH] ensure_token failed: {e}", flush=True)
            shared_state["system_status"]["auth"] = "ERROR"
            _cache["login_cooldown"] = now + LOGIN_COOLDOWN_SECONDS

    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
    }


def request(method, url, json=None):
    now = time.time()

    # Prevent hammering API
    if now < _cache["request_cooldown"]:
        print("[REQUEST] Cooldown active, returning cached positions/account", flush=True)
        if "positions" in url:
            return _cache["positions"]["data"]
        if "accounts" in url:
            return _cache["account"]["data"]

    headers = get_headers()

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        if response.status_code >= 400:
            print(f"[ERROR] {method} {url} → {response.status_code}", flush=True)
            print(f"[ERROR] Response: {response.text}", flush=True)

            # If rate-limited, activate cooldown
            if "too-many.requests" in response.text:
                _cache["request_cooldown"] = now + CACHE_SECONDS
                _cache["login_cooldown"] = now + LOGIN_COOLDOWN_SECONDS

        return response

    except Exception as e:
        print(f"[ERROR] Request failed: {e}", flush=True)
        return None


# ============================
# POSITIONS (CACHED)
# ============================

def get_positions():
    now = time.time()

    # Return cached if fresh
    if now - _cache["positions"]["ts"] < CACHE_SECONDS:
        return _cache["positions"]["data"]

    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        return shared_state.get("positions", [])

    try:
        data = response.json()
        positions = data.get("positions", [])
        positions = clean_structure(positions)

        shared_state["positions"] = positions
        _cache["positions"]["data"] = positions
        _cache["positions"]["ts"] = now

        return positions

    except Exception as e:
        print(f"[POSITIONS] Failed to parse positions: {e}", flush=True)
        return shared_state.get("positions", [])


# ============================
# ACCOUNT (CACHED)
# ============================

def get_account():
    now = time.time()

    if now - _cache["account"]["ts"] < CACHE_SECONDS:
        return _cache["account"]["data"]

    response = request("GET", API_ACCOUNTS)

    if not response or response.status_code != 200:
        return shared_state.get("account", {})

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        account = accounts[0] if accounts else {}
        account = clean_structure(account)

        shared_state["account"] = account
        _cache["account"]["data"] = account
        _cache["account"]["ts"] = now

        return account

    except Exception as e:
        print(f"[ACCOUNT] Failed to parse account: {e}", flush=True)
        return shared_state.get("account", {})


# ============================
# ENRICHERS
# ============================

def enrich_account(raw):
    if not raw:
        return {}

    bal = raw.get("balance", {})

    equity = clean_value(bal.get("balance", 0))
    funds = clean_value(bal.get("deposit", 0))
    pnl = clean_value(bal.get("profitLoss", 0))
    available_raw = clean_value(bal.get("available", 0))

    margin = equity - available_raw
    available = max(0, equity - margin)

    margin_warning = None
    if available <= 0:
        margin_warning = "⚠ Margin Warning: Available balance is zero or negative."

    return {
        "funds": round(funds, 2),
        "balance": round(equity, 2),
        "pnl": round(pnl, 2),
        "available": round(available, 2),
        "margin": round(margin, 2),
        "available_color": "red" if available <= 0 else "lime",
        "margin_warning": margin_warning
    }


def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        profit = clean_value(pos.get("upl", 0))
        direction = pos.get("direction")

        position_id = pos.get("dealId")
        ticker = market.get("symbol")
        epic = market.get("epic")
        size = pos.get("size")
        entry_price = pos.get("level")

        current_price = None
        if direction == "SELL":
            current_price = market.get("bid")
        else:
            current_price = market.get("offer")

        sl = pos.get("stopLevel")
        tp = pos.get("profitLevel")
        currency = pos.get("currency")

        enriched.append({
            "position_id": position_id,
            "ticker": ticker,
            "epic": epic,
            "size": size,
            "entry_price": entry_price,
            "current_price": current_price,
            "side": direction,
            "pnl": round(profit, 2) if profit is not None else None,
            "sl": sl,
            "tp": tp,
            "currency": currency,

            # Legacy keys
            "id": position_id,
            "price": entry_price,
            "profit": round(profit, 2) if profit is not None else None,
            "stopLevel": sl,
            "limitLevel": tp,
            "direction": direction
        })

    return enriched


# ============================
# DAILY REPORT
# ============================

def get_daily_report():
    try:
        report_data = report.get_daily_report()
        shared_state["daily_report"] = report_data
        return report_data
    except Exception as e:
        print(f"[REPORT] Failed to load daily report: {e}", flush=True)
        return shared_state.get("daily_report", {})


def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()


def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
