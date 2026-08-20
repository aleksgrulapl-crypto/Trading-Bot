# ============================
# SESSION MODULE (STABLE POSITION PIPELINE)
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

    if "/positions/" in url and "/close" in url:
        print("[SESSION] CLOSE REQUEST", flush=True)
        print("[SESSION] METHOD  →", method, flush=True)
        print("[SESSION] URL     →", url, flush=True)
        print("[SESSION] BODY    →", json, flush=True)

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


def get_positions():
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        print("[SESSION] get_positions → non-200 or no response, returning []", flush=True)
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


def enrich_positions(raw_positions):
    """
    Convert raw Capital.com positions into dashboard-friendly objects.
    id  = dealId (for legacy use)
    dealId = explicit field for close button and logging.
    """
    enriched = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        deal_id = pos.get("dealId")
        ticker = market.get("symbol")
        direction = pos.get("direction")
        size = pos.get("size")
        entry_price = pos.get("level")

        # CORRECT:
        # LONG (BUY) → current_price = bid (you would sell)
        # SHORT (SELL) → current_price = offer (you would buy back)
        if direction == "BUY":
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

def get_account():
    now = time.time()
    if now - _cache["account"]["ts"] < 1.0:
        return _cache["account"]["data"]

    response = request("GET", API_ACCOUNT)

    if not response or response.status_code != 200:
        print("[SESSION] get_account → non-200 or no response, returning cached", flush=True)
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

    # Capital.com fields:
    # funds       → cash
    # balance     → sometimes same as funds, sometimes separate
    # profitLoss  → floating PnL
    # available   → available to trade
    # margin      → used margin

    funds = bal.get("funds", 0)
    balance = bal.get("balance", funds)
    pnl = bal.get("profitLoss", 0)
    available = bal.get("available", 0)
    margin = bal.get("margin", 0)

    margin_warning = None
    if available < 0:
        margin_warning = "⚠ Margin Warning: Available balance is negative."

    return {
        "funds": round(funds, 2),          # cash
        "balance": round(balance, 2),      # will be turned into live equity in dashboard
        "pnl": round(pnl, 2),
        "available": round(available, 2),
        "margin": round(margin, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": margin_warning
    }

def compute_used_margin(raw_positions):
    total = 0.0
    for item in raw_positions:
        pos = item.get("position", {})
        size = pos.get("size")
        level = pos.get("level")
        leverage = pos.get("leverage") or 1
        try:
            size_f = float(size)
            level_f = float(level)
            lev_f = float(leverage)
            total += (size_f * level_f) / lev_f
        except Exception:
            pass
    return round(total, 2)


def verify_epic(symbol):
    symbol = symbol.upper()

    if symbol in EPIC_MAP:
        return {"epic": EPIC_MAP[symbol], "source": "map"}

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


def get_daily_report():
    try:
        report_data = report.get_daily_report()
        shared_state["daily_report"] = report_data
        return report_data
    except Exception as e:
        print(f"[REPORT] Failed to load daily report: {e}", flush=True)
        return {}


def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()

def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
