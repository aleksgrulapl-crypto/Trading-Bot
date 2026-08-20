# ============================
# SESSION MODULE (CLEAN + CAPITAL.COM EXACT VALUES)
# ============================

import time
import pprint
from auth import auth
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report

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
    "account": {"ts": 0, "data": {}}
}

# ---------------------------------------------------------
# REQUEST WRAPPER
# ---------------------------------------------------------

def get_headers():
    auth.ensure_token()
    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def request(method, url, json=None):
    headers = get_headers()

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        if response.status_code >= 400:
            print(f"[ERROR] {method} {url} → {response.status_code}", flush=True)
            print(f"[ERROR] Response: {response.text}", flush=True)

        return response

    except Exception as e:
        import traceback
        print("[ERROR] Request failed:", flush=True)
        traceback.print_exc()
        return None


# ---------------------------------------------------------
# POSITIONS
# ---------------------------------------------------------

def get_positions():
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        print("[SESSION] get_positions → non-200 or no response", flush=True)
        return []

    try:
        data = response.json()
        positions = data.get("positions", [])

        if positions:
            print("\n[DEBUG] RAW POSITION SAMPLE:")
            pprint.pprint(positions[0], width=200)
            print("\n")

        return positions

    except Exception as e:
        print(f"[SESSION] Failed to parse positions: {e}", flush=True)
        return []


def normalize_direction(direction):
    if not direction:
        return None
    d = direction.upper()
    if d == "BUY":
        return "Long"
    if d == "SELL":
        return "Short"
    return direction.capitalize()


def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        deal_id = pos.get("dealId")
        ticker = market.get("symbol")
        direction_raw = pos.get("direction")
        direction = normalize_direction(direction_raw)

        size = pos.get("size")
        entry_price = pos.get("level")

        # BUY → bid, SELL → offer
        if direction_raw == "BUY":
            current_price = market.get("bid")
        else:
            current_price = market.get("offer")

        profit = pos.get("upl", 0)

        enriched.append({
            "id": deal_id,
            "dealId": deal_id,
            "dealReference": pos.get("dealReference"),
            "ticker": ticker,
            "epic": market.get("epic"),
            "size": size,
            "price": entry_price,
            "current_price": current_price,
            "direction": direction,
            "profit": round(profit, 2),
            "stopLevel": pos.get("stopLevel"),
            "limitLevel": pos.get("profitLevel"),
            "currency": pos.get("currency"),
            "signature": f"{ticker}|{direction}|{size}|{entry_price}",
        })

    return enriched


# ---------------------------------------------------------
# ACCOUNT
# ---------------------------------------------------------

def get_account():
    now = time.time()
    if now - _cache["account"]["ts"] < 1.0:
        return _cache["account"]["data"]

    response = request("GET", API_ACCOUNT)

    if not response or response.status_code != 200:
        print("[SESSION] get_account → non-200 or no response", flush=True)
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


def enrich_account(raw):
    if not raw:
        return {}

    bal = raw.get("balance", {})

    balance = bal.get("balance", 0)
    pnl = bal.get("profitLoss", 0)
    available = bal.get("available", 0)
    margin = bal.get("margin", 0)

    equity = balance + pnl

    return {
        "equity": round(equity, 2),
        "balance": round(balance, 2),
        "pnl": round(pnl, 2),
        "available": round(available, 2),
        "margin": round(margin, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": "⚠ Margin Warning" if available < 0 else None
    }
