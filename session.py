# ============================
# SESSION MODULE (STABLE + CACHED + UPDATED)
# ============================

import requests
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET, EPIC_MAP
from utils import timestamp
import report
from trade_log import load_log

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


def get_headers():
    try:
        auth.ensure_token()
        shared_state["system_status"]["auth"] = "OK"
    except Exception as e:
        print(f"[AUTH] ensure_token failed: {e}", flush=True)
        shared_state["system_status"]["auth"] = "ERROR"

    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
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
        print(f"[ERROR] Request failed: {e}", flush=True)
        return None


def get_positions():
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)
    if not response or response.status_code != 200:
        return shared_state.get("positions", [])

    try:
        data = response.json()
        positions = data.get("positions", [])
        shared_state["positions"] = positions
        return positions
    except Exception as e:
        print(f"[POSITIONS] Failed to parse positions: {e}", flush=True)
        return shared_state.get("positions", [])


def get_account():
    response = request("GET", API_ACCOUNTS)
    if not response or response.status_code != 200:
        return shared_state.get("account", {})

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        account = accounts[0] if accounts else {}
        shared_state["account"] = account
        return account
    except Exception as e:
        print(f"[ACCOUNT] Failed to parse account: {e}", flush=True)
        return shared_state.get("account", {})


def enrich_account(raw):
    """
    Map Capital.com balance fields to dashboard fields:

    - Funds  = balance.balance
    - Balance (Equity) = balance.equity
    - PnL    = balance.profitLoss
    """
    if not raw:
        return {}

    bal = raw.get("balance", {})

    funds = bal.get("balance", 0)          # Capital "Funds"
    equity = bal.get("equity", funds)      # Capital "Equity"
    pnl = bal.get("profitLoss", 0)
    available = bal.get("available", 0)

    margin_warning = None
    if available < 0:
        margin_warning = "⚠ Margin Warning: Available balance is negative."

    return {
        "funds": round(funds, 2),
        "balance": round(equity, 2),       # label "Balance" shows Equity
        "pnl": round(pnl, 2),
        "available": round(available, 2),
        "available_color": "red" if available < 0 else "lime",
        "margin_warning": margin_warning
    }


def verify_epic(symbol):
    if not symbol:
        return {"epic": None, "source": "invalid_symbol"}

    symbol = symbol.upper()
    print(f"[EPIC] verify_epic called with symbol={symbol}", flush=True)

    if symbol in EPIC_MAP:
        epic = EPIC_MAP[symbol]
        print(f"[EPIC] Local EPIC map hit: {symbol} → {epic}", flush=True)
        return {"epic": epic, "source": "local"}

    try:
        url = f"{API_MARKET}/{symbol}"
        r = request("GET", url)

        if not r or r.status_code != 200:
            print(f"[EPIC] API lookup failed for {symbol}", flush=True)
            return {"epic": None, "source": "api_error"}

        data = r.json()
        epic = data.get("instrument", {}).get("epic")

        if epic:
            print(f"[EPIC] API EPIC resolved: {symbol} → {epic}", flush=True)
            return {"epic": epic, "source": "api"}

        print(f"[EPIC] No EPIC found for {symbol}", flush=True)
        return {"epic": None, "source": "not_found"}

    except Exception as e:
        print(f"[EPIC] Exception during lookup: {e}", flush=True)
        return {"epic": None, "source": "exception"}


def enrich_positions(raw_positions):
    enriched = []

    for item in raw_positions:
        pos = item.get("position", {})
        market = item.get("market", {})

        profit = pos.get("upl", 0)
        direction = pos.get("direction")

        enriched.append({
            "position_id": pos.get("dealId"),
            "ticker": market.get("symbol"),
            "epic": market.get("epic"),
            "size": pos.get("size"),
            "entry_price": pos.get("level"),
            "current_price": market.get("bid") if direction == "SELL" else market.get("offer"),
            "side": direction,
            "pnl": round(profit, 2),
            "sl": pos.get("stopLevel"),
            "tp": pos.get("profitLevel"),
            "currency": pos.get("currency")
        })

    return enriched


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
