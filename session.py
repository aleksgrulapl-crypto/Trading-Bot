# ============================
# SESSION MODULE (STABLE + CACHED + UPDATED)
# ============================

import time
from math import isnan

from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET, EPIC_MAP
from utils import timestamp

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

_cache = {
    "positions": {"data": None, "ts": 0},
    "account": {"data": None, "ts": 0},
    "request_cooldown": 0,
    "login_cooldown": 0
}

CACHE_SECONDS = 5
LOGIN_COOLDOWN_SECONDS = 30


# ---------------------------------------------------------
# CLEANERS
# ---------------------------------------------------------

def clean_value(v):
    try:
        if v is None:
            return None
        if isinstance(v, float) and isnan(v):
            return None
        return v
    except Exception:
        return None


def clean_structure(obj):
    if isinstance(obj, dict):
        return {k: clean_structure(clean_value(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_structure(clean_value(x)) for x in obj]
    return clean_value(obj)


# ---------------------------------------------------------
# AUTH + REQUEST (PATCHED)
# ---------------------------------------------------------

def get_headers():
    now = time.time()

    # Only refresh token if missing AND cooldown expired
    if (auth.cst is None or auth.xst is None) and now >= _cache["login_cooldown"]:
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

    # Respect cooldown to avoid rate-limit
    if now < _cache["request_cooldown"]:
        print("[REQUEST] Cooldown active, returning cached positions/account if available", flush=True)

        if "positions" in url and _cache["positions"]["data"] is not None:
            class DummyResponse:
                status_code = 200

                def json(self_inner):
                    return {"positions": _cache["positions"]["data"]}

            return DummyResponse()

        if "accounts" in url and _cache["account"]["data"] is not None:
            class DummyResponse:
                status_code = 200

                def json(self_inner):
                    return {"accounts": [_cache["account"]["data"]]}

            return DummyResponse()

    headers = get_headers()

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        if response.status_code >= 400:
            print(f"[ERROR] {method} {url} → {response.status_code}", flush=True)
            print(f"[ERROR] Response: {response.text}", flush=True)

            if "too-many.requests" in response.text:
                _cache["request_cooldown"] = now + CACHE_SECONDS
                _cache["login_cooldown"] = now + LOGIN_COOLDOWN_SECONDS

        return response

    except Exception as e:
        print(f"[ERROR] Request failed: {e}", flush=True)
        return None


# ---------------------------------------------------------
# POSITIONS
# ---------------------------------------------------------

def get_positions():
    now = time.time()

    if now - _cache["positions"]["ts"] < CACHE_SECONDS and _cache["positions"]["data"] is not None:
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


def fetch_positions_from(url: str):
    response = request("GET", url)
    if not response:
        return {}
    try:
        return response.json()
    except Exception as e:
        print(f"[POSITIONS] fetch_positions_from parse failed: {e}", flush=True)
        return {}


# ---------------------------------------------------------
# ACCOUNT
# ---------------------------------------------------------

def get_account():
    now = time.time()

    if now - _cache["account"]["ts"] < CACHE_SECONDS and _cache["account"]["data"] is not None:
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


# ---------------------------------------------------------
# ENRICHERS
# ---------------------------------------------------------

def enrich_account(raw):
    """
    Capital.com account JSON typically exposes:
    - balance: overall equity
    - deposit: cash funds
    - profitLoss: current PnL
    - available: free margin
    We derive margin as equity - available.
    """
    if not raw:
        return {}

    bal = raw.get("balance", {})

    equity = clean_value(bal.get("balance", 0))
    funds = clean_value(bal.get("deposit", 0))
    pnl = clean_value(bal.get("profitLoss", 0))
    available = clean_value(bal.get("available", 0))

    if equity is None:
        equity = 0
    if funds is None:
        funds = 0
    if pnl is None:
        pnl = 0
    if available is None:
        available = 0

    margin = equity - available

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

        current_price = market.get("bid") if direction == "SELL" else market.get("offer")

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
            "id": position_id,
            "price": entry_price,
            "profit": round(profit, 2) if profit is not None else None,
            "stopLevel": sl,
            "limitLevel": tp,
            "direction": direction
        })

    return enriched


# ---------------------------------------------------------
# EPIC RESOLUTION
# ---------------------------------------------------------

def verify_epic(symbol: str):
    epic = EPIC_MAP.get(symbol)
    if epic:
        return {"epic": epic, "symbol": symbol, "source": "map"}

    try:
        url = f"{API_MARKET}/{symbol}"
        resp = request("GET", url)
        if resp and resp.status_code == 200:
            data = resp.json()
            snap = data.get("snapshot", {})
            epic_from_api = data.get("epic") or snap.get("epic")
            if epic_from_api:
                return {"epic": epic_from_api, "symbol": symbol, "source": "api"}
    except Exception as e:
        print(f"[EPIC] API lookup failed for {symbol}: {e}", flush=True)

    print(f"[EPIC] No epic found for symbol {symbol}", flush=True)
    return {"epic": None, "symbol": symbol, "source": "none"}


# ---------------------------------------------------------
# DAILY REPORT (LEGACY, SAFE NO-OP)
# ---------------------------------------------------------

def get_daily_report():
    # Kept for compatibility; can be wired later if needed.
    return shared_state.get("daily_report", {})


def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()


def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
