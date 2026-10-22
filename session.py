# session.py
# ============================
# SESSION MODULE (CLEAN ACCOUNT VALUES — BALANCE STATIC, EQUITY = BALANCE + PNL)

import time
import threading
import pprint
import logging
from typing import Any, Dict, List, Optional

from auth import auth
import config
from config import API_POSITIONS, API_ACCOUNT, API_MARKET, EPIC_MAP
from utils import timestamp
import report

logger = logging.getLogger("session")
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [session] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

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

_cache: Dict[str, Any] = {
    "account": {"ts": 0.0, "data": {}}
}
_ACCOUNT_CACHE_TTL = float(getattr(config, "CACHE_TTL_SECONDS", 2))


def get_headers() -> Optional[Dict[str, str]]:
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


_positions_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_positions_lock = threading.Lock()
_positions_state: Dict[str, Any] = {"failures": 0, "backoff_until": 0.0}
_POSITIONS_CACHE_TTL = float(getattr(config, "POSITIONS_CACHE_TTL_SECONDS", 3))
_POSITIONS_MAX_BACKOFF = float(getattr(config, "POSITIONS_MAX_BACKOFF_SECONDS", 30))
_POSITIONS_STALE_MAX_AGE = float(getattr(config, "POSITIONS_STALE_MAX_AGE_SECONDS", 120))


def get_positions(fresh: bool = False) -> Optional[List[dict]]:
    """Return live broker positions, or None if they could not be fetched.

    None (a timeout / non-200) is deliberately distinct from [] (genuinely no
    open positions) so callers never mistake an outage for "everything
    closed". Results are cached briefly and shared by the scheduler jobs and
    dashboard (one broker call instead of several per tick), and after a
    failure further calls back off exponentially instead of hammering a slow
    broker API. ``fresh=True`` bypasses the cache and back-off.
    """
    with _positions_lock:
        now = time.monotonic()
        if not fresh:
            if _positions_cache["data"] is not None and now - _positions_cache["ts"] < _POSITIONS_CACHE_TTL:
                return list(_positions_cache["data"])
            if now < _positions_state["backoff_until"]:
                return None

        url = f"{API_POSITIONS}?includeProfitLoss=true"
        response = request("GET", url)

        positions = None
        if response is not None and response.status_code == 200:
            try:
                data = response.json() or {}
                positions = data.get("positions", []) or []
            except Exception:
                logger.exception("get_positions: failed to parse JSON")

        if positions is None:
            _positions_state["failures"] += 1
            delay = min(_POSITIONS_MAX_BACKOFF, 2.0 ** _positions_state["failures"])
            _positions_state["backoff_until"] = time.monotonic() + delay
            logger.debug("get_positions: unavailable; backing off %.0fs", delay)
            return None

        _positions_state["failures"] = 0
        _positions_state["backoff_until"] = 0.0
        _positions_cache["ts"] = time.monotonic()
        _positions_cache["data"] = positions
        if getattr(config, "DEBUG_LOGS", False) and positions:
            logger.debug("\n[DEBUG] RAW POSITION SAMPLE:")
            pprint.pprint(positions[0], width=200)
            logger.debug("\n")
        return list(positions)


def last_known_positions() -> Optional[List[dict]]:
    """Most recent successfully fetched positions if still recent enough for
    display purposes (never use for close detection), else None."""
    data = _positions_cache["data"]
    if data is None or time.monotonic() - _positions_cache["ts"] > _POSITIONS_STALE_MAX_AGE:
        return None
    return list(data)


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
    enriched: List[dict] = []

    for item in raw_positions or []:
        pos = (item.get("position") or {}) or {}
        market = (item.get("market") or {}) or {}

        deal_id = pos.get("dealId")
        ticker = market.get("symbol") or pos.get("instrumentName") or None
        direction_raw = pos.get("direction")
        direction = _normalize_direction(direction_raw)
        # provide both names to avoid mismatch across modules
        side = direction
        size = pos.get("size")
        entry_price = pos.get("level") or pos.get("entry_price") or pos.get("entryPrice")

        current_price = None
        try:
            # For a Long (BUY) the current price to compare is the bid (price you can sell at)
            if direction_raw and str(direction_raw).upper() == "BUY":
                current_price = market.get("bid")
            else:
                current_price = market.get("offer")
        except Exception:
            current_price = None

        profit_raw = pos.get("upl", pos.get("profit", 0))
        try:
            profit_val = round(float(profit_raw or 0), 2)
        except Exception:
            profit_val = 0.0

        enriched.append({
            "id": deal_id,
            "dealId": deal_id,
            "dealReference": pos.get("dealReference"),
            "ticker": ticker,
            "epic": market.get("epic"),
            "size": size,
            # keep legacy key 'price' but add canonical 'entry_price'
            "price": entry_price,
            "entry_price": entry_price,
            "current_price": current_price,
            "direction": direction,
            "side": side,
            "profit": profit_val,
            "upl": profit_val,
            "stopLevel": pos.get("stopLevel"),
            "limitLevel": pos.get("profitLevel"),
            "currency": pos.get("currency"),
            "signature": f"{ticker}|{direction}|{size}|{entry_price}",
            # keep raw nested objects for any downstream code that expects them
            "raw_position": pos,
            "raw_market": market,
        })

    return enriched


def get_account() -> dict:
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
    Avoid double-counting PnL if API 'balance' is actually equity.
    """
    if not raw:
        return {}

    bal = (raw.get("balance") or {}) or {}

    reported_funds = bal.get("funds", None)
    reported_balance = bal.get("balance", None)
    reported_pnl = bal.get("profitLoss", None)
    reported_available = bal.get("available", None)

    if reported_funds is not None:
        balance_val = reported_funds
    else:
        if reported_balance is not None and reported_pnl is not None:
            try:
                balance_val = float(reported_balance) - float(reported_pnl)
            except Exception:
                balance_val = reported_balance
        else:
            balance_val = reported_balance if reported_balance is not None else 0

    pnl_val = reported_pnl if reported_pnl is not None else 0
    available_val = reported_available if reported_available is not None else 0

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


def verify_epic(symbol: str) -> dict:
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
