# session.py
# ============================
# SESSION MODULE (CLEAN ACCOUNT VALUES — BALANCE STATIC, EQUITY = BALANCE + PNL)
# ============================

import time
import pprint
import logging
from typing import Any, Dict, List, Optional

from auth import auth
import config
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report

# logger for this module (do not call basicConfig here)
logger = logging.getLogger("session")
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [session] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

# Shared application state
shared_state: Dict[str, Any] = {
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

# Simple in-memory cache for account (TTL configurable)
_cache: Dict[str, Any] = {
    "account": {"ts": 0.0, "data": {}}
}
_ACCOUNT_CACHE_TTL = float(getattr(config, "CACHE_TTL_SECONDS", 2))


# -------------------------
# Helpers
# -------------------------

def get_headers() -> Optional[Dict[str, str]]:
    """
    Return headers for authenticated requests or None if auth not available.
    """
    if not auth.ensure_token():
        logger.debug("get_headers: auth.ensure_token failed")
        return None

    return {
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def request(method: str, url: str, json: Optional[dict] = None, **kwargs):
    """
    Unified request wrapper that uses auth.request if available.
    Returns requests.Response or None.
    """
    # Prefer auth.request wrapper which handles tokens and retries
    try:
        resp = auth.request(method, url, json=json, **kwargs)
        if resp is None:
            logger.debug("request: auth.request returned None for %s %s", method, url)
            return None
        if getattr(resp, "status_code", 0) >= 400:
            logger.warning("HTTP %s %s -> %s", method, url, resp.status_code)
            logger.debug("Response body: %s", getattr(resp, "text", "")[:1000])
        return resp
    except Exception:
        logger.exception("request: unexpected exception for %s %s", method, url)
        return None


# -------------------------
# Positions and account
# -------------------------

def get_positions() -> List[dict]:
    """
    Fetch live positions from API. Returns list (possibly empty).
    """
    url = f"{API_POSITIONS}?includeProfitLoss=true"
    response = request("GET", url)

    if not response or response.status_code != 200:
        logger.debug("get_positions: non-200 or no response")
        return []

    try:
        data = response.json() or {}
        positions = data.get("positions", []) or []
        if getattr(config, "DEBUG_LOGS", False) and positions:
            logger.debug("\n[DEBUG] RAW POSITION SAMPLE:")
            pprint.pprint(positions[0], width=200)
            logger.debug("\n")
        return positions
    except Exception:
        logger.exception("get_positions: failed to parse JSON")
        return []


def _normalize_direction(direction: Optional[str]) -> Optional[str]:
    if not direction:
        return None
    d = str(direction).upper()
    if d == "BUY":
        return "Long"
    if d == "SELL":
        return "Short"
    return d.capitalize()


def enrich_positions(raw_positions: List[dict]) -> List[dict]:
    """
    Convert raw API positions into normalized, UI-friendly dicts.
    """
    enriched: List[dict] = []

    for item in raw_positions or []:
        pos = (item.get("position") or {}) or {}
        market = (item.get("market") or {}) or {}

        deal_id = pos.get("dealId")
        ticker = market.get("symbol") or pos.get("instrumentName") or None
        direction_raw = pos.get("direction")
        direction = _normalize_direction(direction_raw)
        size = pos.get("size")
        entry_price = pos.get("level")

        # choose current price depending on direction
        current_price = None
        try:
            if direction_raw and str(direction_raw).upper() == "BUY":
                current_price = market.get("bid")
            else:
                current_price = market.get("offer")
        except Exception:
            current_price = None

        profit = pos.get("upl", 0)

        try:
            profit_val = round(float(profit or 0), 2)
        except Exception:
            profit_val = 0.0

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
            "profit": profit_val,
            "stopLevel": pos.get("stopLevel"),
            "limitLevel": pos.get("profitLevel"),
            "currency": pos.get("currency"),
            "signature": f"{ticker}|{direction}|{size}|{entry_price}",
        })

    return enriched


def get_account() -> dict:
    """
    Fetch account payload with short caching to reduce API calls.
    Returns raw account dict (as returned by API) or cached value.
    """
    now = time.time()
    cache_entry = _cache.get("account", {})
    if now - cache_entry.get("ts", 0.0) < _ACCOUNT_CACHE_TTL:
        return cache_entry.get("data", {})

    response = request("GET", API_ACCOUNT)
    if not response or response.status_code != 200:
        logger.warning("get_account: non-200 or no response; returning cached data")
        return cache_entry.get("data", {})

    try:
        data = response.json() or {}
        accounts = data.get("accounts", []) or []
        acc = accounts[0] if accounts else {}

        _cache["account"]["ts"] = now
        _cache["account"]["data"] = acc
        if getattr(config, "DEBUG_LOGS", False):
            logger.debug("get_account: raw account payload: %s", acc)
        return acc
    except Exception:
        logger.exception("get_account: failed to parse JSON")
        return cache_entry.get("data", {})


def enrich_account(raw: dict) -> dict:
    """
    Normalize account values:
      - balance: static cash/funds
      - pnl: floating profit/loss
      - available: available to trade
      - equity: computed as balance + pnl
    """
    if not raw:
        return {}

    bal = (raw.get("balance") or {}) or {}

    # Prefer 'funds' as the static Balance. If missing, fall back to 'balance' key.
    if "funds" in bal:
        balance_val = bal.get("funds", 0)
    else:
        balance_val = bal.get("balance", 0)
        logger.debug("enrich_account: 'funds' not present; falling back to 'balance'")

    pnl_val = bal.get("profitLoss", 0)
    available_val = bal.get("available", 0)

    try:
        balance = float(balance_val or 0)
    except Exception:
        balance = 0.0
    try:
        pnl = float(pnl_val or 0)
    except Exception:
        pnl = 0.0
    try:
        available = float(available_val or 0)
    except Exception:
        available = 0.0

    equity = balance + pnl

    account_obj = {
        "equity": round(equity, 2),
        "balance": round(balance, 2),
        "pnl": round(pnl, 2),
        "available": round(available, 2)
    }

    if getattr(config, "DEBUG_LOGS", False):
        logger.debug("enrich_account: %s", account_obj)

    return account_obj


# -------------------------
# EPIC lookup
# -------------------------

def verify_epic(symbol: str) -> dict:
    """
    Resolve a ticker symbol to an EPIC. First consult EPIC_MAP, then API lookup.
    Returns dict: {'epic': str or None, 'source': 'map'|'api'|'not_found'|'api_error'|'exception'}
    """
    if not symbol:
        return {"epic": None, "source": "invalid"}

    symbol_up = str(symbol).upper()
    if symbol_up in EPIC_MAP:
        return {"epic": EPIC_MAP[symbol_up], "source": "map"}

    try:
        url = f"{API_MARKET}/{symbol_up}"
        r = request("GET", url)
        if not r or r.status_code != 200:
            logger.debug("verify_epic: API lookup failed for %s", symbol_up)
            return {"epic": None, "source": "api_error"}

        data = r.json() or {}
        epic = (data.get("instrument") or {}).get("epic")
        if epic:
            return {"epic": epic, "source": "api"}
        logger.debug("verify_epic: no EPIC found for %s", symbol_up)
        return {"epic": None, "source": "not_found"}
    except Exception:
        logger.exception("verify_epic: exception during lookup for %s", symbol_up)
        return {"epic": None, "source": "exception"}


# -------------------------
# Reports and state updates
# -------------------------

def get_daily_report() -> dict:
    try:
        report_data = report.get_daily_report()
        shared_state["daily_report"] = report_data
        return report_data
    except Exception:
        logger.exception("get_daily_report: failed to load daily report")
        return {}


def update_last_trade() -> None:
    shared_state.setdefault("system_status", {})["last_trade"] = timestamp()


def update_last_webhook() -> None:
    shared_state.setdefault("system_status", {})["last_webhook"] = timestamp()
