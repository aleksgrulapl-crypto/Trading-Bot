# session.py
# ============================
# SESSION MODULE (CLEAN ACCOUNT VALUES — BALANCE STATIC, EQUITY = BALANCE + PNL)
# ============================

import time
import pprint
import logging
from auth import auth
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report

# Optional debug flag in config.py: DEBUG_LOGS = True/False
try:
    from config import DEBUG_LOGS
except Exception:
    DEBUG_LOGS = False

logger = logging.getLogger("session")
if DEBUG_LOGS:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

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

    try:
        response = auth.session.request(method, url, headers=headers, json=json)

        if response is None:
            logger.debug("[REQUEST] No response object returned")
            return None

        if response.status_code >= 400:
            logger.warning("[ERROR] %s %s → %s", method, url, response.status_code)
            logger.debug("[ERROR] Response: %s", response.text)

        return response

    except Exception:
        logger.exception("[ERROR] Request failed:")
        return None


def get_positions():
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        if DEBUG_LOGS:
            logger.debug("[SESSION] get_positions → non-200 or no response")
        return []

    try:
        data = response.json()
        positions = data.get("positions", [])

        if DEBUG_LOGS and positions:
            logger.debug("\n[DEBUG] RAW POSITION SAMPLE:")
            pprint.pprint(positions[0], width=200)
            logger.debug("\n")

        return positions

    except Exception:
        logger.exception("[SESSION] Failed to parse positions:")
        return []


def _normalize_direction(direction):
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
        pos = item.get("position", {}) or {}
        market = item.get("market", {}) or {}

        deal_id = pos.get("dealId")
        ticker = market.get("symbol") or pos.get("instrumentName") or None
        direction_raw = pos.get("direction")
        direction = _normalize_direction(direction_raw)
        size = pos.get("size")
        entry_price = pos.get("level")

        # choose current price depending on direction
        # keep existing behavior: BUY -> bid, SELL -> offer
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
            "profit": round(float(profit or 0), 2),
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
        logger.warning("[SESSION] get_account → non-200 or no response")
        return _cache["account"]["data"]

    try:
        data = response.json()
        accounts = data.get("accounts", [])
        acc = accounts[0] if accounts else {}

        _cache["account"]["ts"] = now
        _cache["account"]["data"] = acc
        if DEBUG_LOGS:
            logger.debug("[SESSION] Raw account payload: %s", acc)
        return acc

    except Exception:
        logger.exception("[SESSION] Failed to parse account:")
        return _cache["account"]["data"]


def enrich_account(raw):
    """
    Correct mapping for Capital.com account payload:
      - funds      : static cash balance (use as Balance)
      - profitLoss : floating PnL (use as PnL)
      - available  : available to trade
      - balance    : reported equity (we compute equity ourselves)
    """
    if not raw:
        return {}

    bal = raw.get("balance", {}) or {}

    # Prefer 'funds' as the static Balance. If missing, fall back to 'balance' but log it.
    if "funds" in bal:
        balance = bal.get("funds", 0)
    else:
        # fallback: some accounts may return 'balance' as funds; log for visibility
        balance = bal.get("balance", 0)
        logger.debug("[SESSION] 'funds' not present in account.balance; falling back to 'balance' field")

    pnl = bal.get("profitLoss", 0)
    available = bal.get("available", 0)

    try:
        balance = float(balance or 0)
    except Exception:
        balance = 0.0
    try:
        pnl = float(pnl or 0)
    except Exception:
        pnl = 0.0
    try:
        available = float(available or 0)
    except Exception:
        available = 0.0

    equity = balance + pnl

    account_obj = {
        "equity": round(equity, 2),
        "balance": round(balance, 2),
        "pnl": round(pnl, 2),
        "available": round(available, 2)
    }

    if DEBUG_LOGS:
        logger.debug("[SESSION] Enriched account: %s", account_obj)

    return account_obj


def verify_epic(symbol):
    symbol = symbol.upper()

    if symbol in EPIC_MAP:
        return {"epic": EPIC_MAP[symbol], "source": "map"}

    try:
        url = f"{API_MARKET}/{symbol}"
        r = request("GET", url)

        if not r or r.status_code != 200:
            logger.debug("[EPIC] API lookup failed for %s", symbol)
            return {"epic": None, "source": "api_error"}

        data = r.json()
        epic = data.get("instrument", {}).get("epic")

        if epic:
            return {"epic": epic, "source": "api"}

        logger.debug("[EPIC] No EPIC found for %s", symbol)
        return {"epic": None, "source": "not_found"}

    except Exception:
        logger.exception("[EPIC] Exception during lookup:")
        return {"epic": None, "source": "exception"}


def get_daily_report():
    try:
        report_data = report.get_daily_report()
        shared_state["daily_report"] = report_data
        return report_data
    except Exception:
        logger.exception("[REPORT] Failed to load daily report:")
        return {}


def update_last_trade():
    shared_state["system_status"]["last_trade"] = timestamp()


def update_last_webhook():
    shared_state["system_status"]["last_webhook"] = timestamp()
