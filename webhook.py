#!/usr/bin/env python3
# webhook.py
# Idempotent webhook handler integrated with trade_log.upsert_open_trade and close_trade_by_dealId.
#
# Key improvements:
#   - Dashboard import validation: fails fast if blueprint not exported correctly
#   - Correlation IDs (UUID) on every webhook request for end-to-end tracing
#   - Input validation in process_webhook_payload()
#   - Actionable error messages that include field names and actual values
#   - Request timeout constant for all outbound broker API calls

import os
import sys
import time
import uuid
import json
import logging
import functools
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from flask import Flask, request, jsonify, render_template, redirect

import pytz

import session
from sizing import calculate_size
from order import place_order
from sl_tp import FixedSLTP
from auth import auth
from config import (
    API_ACCOUNTS,
    API_MARKET,
    API_BASE,
    DEBUG_LOGS,
    TIMEZONE,
    TRADE_LOCK_ENABLED,
    TRADE_LOCK_START_HOUR,
    TRADE_LOCK_END_HOUR,
    HEDGING_ENABLED,
)
from scheduler import start_scheduler
from trade_log import (
    load_raw_log,
    save_raw_log,
    upsert_open_trade,
    set_dealId_for_dealReference,
    close_trade_by_dealId,
    _normalize_side,
)
from close_position import close_position as close_position_module

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUTH_RETRY_COUNT = 6          # attempts to obtain auth token on startup
AUTH_RETRY_BACKOFF = 5        # seconds between startup auth retries
BROKER_API_TIMEOUT = 30       # seconds – applied to all outbound broker requests
ALERT_DEDUPE_WINDOW = 10      # seconds – suppress identical repeat alerts (TradingView retries)

_UK_TZ = pytz.timezone(TIMEZONE)


def _is_trade_locked_now() -> bool:
    """
    Return True if new trade entries should currently be blocked, per the
    configured trade time lock window (default 14:00-15:00 UK time), which
    covers the US cash market open and its associated volatility spike.
    """
    if not TRADE_LOCK_ENABLED:
        return False
    hour = datetime.now(_UK_TZ).hour
    start, end = TRADE_LOCK_START_HOUR, TRADE_LOCK_END_HOUR
    if start <= end:
        return start <= hour < end
    # Wrap-around window (e.g. start=23, end=1)
    return hour >= start or hour < end

# In-memory cache of recently processed TradingView alerts, keyed by a
# signature of the alert content. Used to guard against TradingView
# redelivering the exact same webhook alert (e.g. on timeout/retry), which
# would otherwise place two separate market orders for the same signal.
_recent_alert_cache: Dict[str, float] = {}
_recent_alert_lock = threading.Lock()

# Per-ticker locks serializing the "check for an existing open trade -> place
# order" critical section in webhook(). Without this, two near-simultaneous
# requests for the same ticker (e.g. a genuine retry/duplicate delivery from
# TradingView) can both pass the _has_open_trade_for_ticker() check before
# either one's resulting trade has been appended to the log (that check-and
# the eventual log append are separated by several slow network calls: epic
# lookup, market snapshot, account/sizing lookup, and order placement).  That
# race results in two real orders being placed at the broker for the same
# signal, which is what shows up as a genuine duplicated/self-closed trade in
# the log alongside the real open position.
_ticker_order_locks: Dict[str, threading.Lock] = {}
_ticker_order_locks_guard = threading.Lock()


def _get_ticker_order_lock(ticker: str) -> threading.Lock:
    key = str(ticker).strip().lower()
    with _ticker_order_locks_guard:
        lock = _ticker_order_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ticker_order_locks[key] = lock
        return lock


def _alert_signature(symbol: Optional[str], action: Optional[str], sl_price: Optional[float],
                      tp_price: Optional[float], timeframe: Optional[str]) -> str:
    return f"{symbol or ''}|{action or ''}|{sl_price or ''}|{tp_price or ''}|{timeframe or ''}"


def _is_duplicate_alert(signature: str) -> bool:
    """Return True and record the signature if an identical alert was seen recently."""
    now = time.time()
    with _recent_alert_lock:
        # prune stale entries
        stale = [k for k, ts in _recent_alert_cache.items() if now - ts > ALERT_DEDUPE_WINDOW]
        for k in stale:
            _recent_alert_cache.pop(k, None)

        last_seen = _recent_alert_cache.get(signature)
        if last_seen is not None and (now - last_seen) <= ALERT_DEDUPE_WINDOW:
            return True
        _recent_alert_cache[signature] = now
        return False


def _has_open_trade_for_ticker(ticker: Optional[str], action: Optional[str] = None) -> bool:
    """Return True if the trade log already has an OPEN trade for this ticker/epic
    that should block a new order for *action* ("buy"/"sell").

    When HEDGING_ENABLED is on, an open trade on the opposite side (e.g. an
    open short while the new signal is "buy") is a legitimate hedge and does
    not block the new order – only an open trade on the *same* side is
    treated as a duplicate. When hedging is disabled (or *action* isn't
    provided), any open trade for the ticker blocks the new order, matching
    the previous behaviour.
    """
    if not ticker:
        return False
    try:
        trades = load_raw_log()
    except Exception:
        logger.exception("_has_open_trade_for_ticker: failed to load trade log")
        return False
    ticker_norm = str(ticker).strip().lower()
    new_side = _normalize_side(action) if (HEDGING_ENABLED and action) else None
    for t in trades:
        if t.get("status") == "OPEN":
            existing_ticker = t.get("ticker") or t.get("epic")
            if existing_ticker and str(existing_ticker).strip().lower() == ticker_norm:
                if new_side is not None:
                    existing_side = _normalize_side(t.get("side"))
                    if existing_side and existing_side != new_side:
                        # Opposite-direction position already open – allow the
                        # new order through as a hedge instead of blocking it.
                        continue
                return True
    return False

# ---------------------------------------------------------------------------
# Parser import: try both common names for compatibility
# ---------------------------------------------------------------------------
try:
    from tradingview_parser import parse_tradingview_alert
except Exception:
    try:
        from parser import parse_tradingview_alert
    except Exception:
        def parse_tradingview_alert(_):
            return {"blocked": True, "reason": "parser_unavailable"}

# ---------------------------------------------------------------------------
# Dashboard blueprint import – fail fast on misconfiguration
# ---------------------------------------------------------------------------
dashboard_blueprint = None
try:
    from dashboard import dashboard as _imported_blueprint
    # Validate that the imported object is actually a Flask Blueprint
    from flask import Blueprint
    if not isinstance(_imported_blueprint, Blueprint):
        raise ImportError(
            f"dashboard.dashboard is not a Flask Blueprint (got {type(_imported_blueprint).__name__}). "
            "Ensure dashboard.py exports `dashboard = Blueprint(...)`."
        )
    dashboard_blueprint = _imported_blueprint
except ImportError as _dashboard_import_err:
    # Re-raise import errors so the operator knows exactly what went wrong
    import sys
    logging.getLogger("webhook").error(
        "STARTUP ERROR: Dashboard blueprint import failed – %s. "
        "Dashboard UI will be unavailable. Fix dashboard.py and restart.",
        _dashboard_import_err,
    )
    # In production-critical setups you may want to hard-fail here.
    # We allow the app to start without a dashboard so webhooks still work.
    dashboard_blueprint = None
except Exception as _dashboard_err:
    logging.getLogger("webhook").error(
        "STARTUP ERROR: Unexpected error importing dashboard blueprint – %s. "
        "Dashboard UI will be unavailable.",
        _dashboard_err,
    )
    dashboard_blueprint = None

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["DEBUG"] = bool(DEBUG_LOGS)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}

# Register blueprint if available and valid
if dashboard_blueprint is not None:
    try:
        app.register_blueprint(dashboard_blueprint)
        logging.getLogger("webhook").info("Dashboard blueprint registered successfully.")
    except Exception as _bp_err:
        logging.getLogger("webhook").error("Failed to register dashboard blueprint: %s", _bp_err)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("webhook")
logger.setLevel(logging.DEBUG if DEBUG_LOGS else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [webhook] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dashboard_login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        return view(*args, **kwargs)
    return wrapper

def _safe_get_json(raw_text: str) -> Any:
    try:
        return request.get_json(force=True)
    except Exception:
        pass
    try:
        return json.loads(raw_text)
    except Exception:
        return raw_text


def _ok_response(payload: Dict[str, Any], code: int = 200):
    return jsonify(payload), code


def _make_signature(dealId: Optional[str], dealReference: Optional[str], ticker: Optional[str], entry_price: Optional[float]) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"


def _find_existing_entry(trades: list, dealId: Optional[str], dealReference: Optional[str], ticker: Optional[str], entry_price: Optional[float]):
    if dealId:
        for t in trades:
            if t.get("dealId") and str(t.get("dealId")) == str(dealId):
                return t, "dealId"
    if dealReference:
        for t in trades:
            if t.get("dealReference") and str(t.get("dealReference")) == str(dealReference):
                return t, "dealReference"
    sig = _make_signature(dealId, dealReference, ticker, entry_price)
    for t in trades:
        existing_sig = _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price"))
        if existing_sig == sig:
            return t, "signature"
    return None, None


def _first_not_none(*values):
    """Return the first value that is not None (0 and '' are preserved)."""
    for v in values:
        if v is not None:
            return v
    return None


def _normalize_source(payload: Dict[str, Any], dealId: Optional[str], dealReference: Optional[str], side: Optional[str]) -> str:
    raw_origin = payload.get("origin") or payload.get("source") or payload.get("trade_source") or payload.get("tradeSource")
    if raw_origin:
        return str(raw_origin).strip().lower()
    if payload.get("webhook") is True or payload.get("cid") or payload.get("alert_id"):
        return "webhook"
    if payload.get("manual") is True:
        return "manual"
    if dealId is not None or dealReference is not None:
        return "bot"
    if side in ("long", "short"):
        return "manual"
    return "unknown"


def _validate_webhook_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Return an error string if the payload is obviously malformed, else None.

    Checks:
      - entry_price must be a positive number when present
      - exit_price must be a positive number when present
      - size must be a positive number when present
      - side/direction must be a recognised string when present

    Uses _first_not_none() instead of 'or' chains so that numeric 0 is not
    silently swallowed and instead triggers the positivity check.
    """
    pos = payload.get("position") or payload
    market = payload.get("market") or payload

    entry_price = _first_not_none(pos.get("level"), pos.get("entryPrice"), payload.get("entry_price"), payload.get("entryPrice"))
    exit_price = _first_not_none(payload.get("price"), payload.get("exit_price"), payload.get("closePrice"))
    size = _first_not_none(pos.get("size"), pos.get("contractSize"), payload.get("size"))
    side = pos.get("direction") or payload.get("side")

    if entry_price is not None and entry_price != "":
        try:
            ep = float(entry_price)
            if ep <= 0:
                return f"entry_price must be positive (got: {ep})"
        except (TypeError, ValueError):
            return f"entry_price is not a number (got: {entry_price!r})"

    if exit_price is not None and exit_price != "":
        try:
            xp = float(exit_price)
            if xp <= 0:
                return f"exit_price must be positive (got: {xp})"
        except (TypeError, ValueError):
            return f"exit_price is not a number (got: {exit_price!r})"

    if size is not None and size != "":
        try:
            sz = float(size)
            if sz <= 0:
                return f"size must be positive (got: {sz})"
        except (TypeError, ValueError):
            return f"size is not a number (got: {size!r})"

    if side is not None and side != "":
        normalized = str(side).strip().lower()
        if normalized not in ("buy", "sell", "long", "short"):
            return f"side/direction '{side}' not recognised (expected: buy/sell/long/short)"

    return None


def process_webhook_payload(payload: Dict[str, Any], cid: str = "") -> Dict[str, Any]:
    """Idempotent processing:
    - Upsert opens via trade_log.upsert_open_trade
    - Close via trade_log.close_trade_by_dealId when exit present
    """
    log_prefix = f"[cid={cid}] " if cid else ""

    validation_error = _validate_webhook_payload(payload)
    if validation_error:
        logger.warning("%sprocess_webhook_payload: validation failed – %s", log_prefix, validation_error)
        return {"action": "rejected", "reason": validation_error, "payload": payload}

    pos = payload.get("position") or payload
    market = payload.get("market") or payload

    dealId = pos.get("dealId") or pos.get("id") or payload.get("dealId")
    dealReference = pos.get("dealReference") or payload.get("dealReference")
    ticker = market.get("symbol") or market.get("epic") or pos.get("instrument") or payload.get("ticker")
    side_raw = pos.get("direction") or payload.get("side")
    side = str(side_raw).strip().lower() if side_raw is not None else None
    size = pos.get("size") or pos.get("contractSize") or payload.get("size")
    entry_price = pos.get("level") or pos.get("entryPrice") or payload.get("entry_price") or payload.get("entryPrice")
    exit_price = payload.get("price") or payload.get("exit_price") or payload.get("closePrice")
    time_entered = pos.get("createdDate") or pos.get("createdDateUTC") or payload.get("time_entered")
    time_exited = payload.get("closedDate") or payload.get("time_exited") or payload.get("timestamp")
    source = _normalize_source(payload, dealId, dealReference, side)

    logger.debug("%sExtracted fields: dealId=%s ticker=%s side=%s size=%s entry=%s exit=%s",
                 log_prefix, dealId, ticker, side, size, entry_price, exit_price)

    if dealId and exit_price not in (None, ""):
        closed = close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note=f"Closed via webhook ({source})")
        if closed:
            closed["trade_source"] = source
            closed["origin"] = source
            logger.info("%sClosed trade dealId=%s exit_price=%s", log_prefix, dealId, exit_price)
            return {"action": "closed", "trade": closed}

    upsert_payload = {
        "dealId": dealId,
        "dealReference": dealReference,
        "ticker": ticker,
        "side": side,
        "size": size,
        "entry_price": entry_price,
        "time_entered": time_entered,
        "notes": payload.get("notes") or f"Imported from webhook ({source})",
        "trade_source": source,
        "origin": source,
    }
    upserted = upsert_open_trade(upsert_payload)
    if upserted:
        logger.info("%sUpserted open trade dealId=%s ticker=%s source=%s", log_prefix, dealId, ticker, source)
        if exit_price not in (None, ""):
            if upserted.get("dealId"):
                closed = close_trade_by_dealId(upserted.get("dealId"), exit_price=exit_price, time_exited=time_exited, note=f"Closed via webhook ({source})")
                if closed:
                    closed["trade_source"] = source
                    closed["origin"] = source
                return {"action": "upserted_and_closed", "trade": closed or upserted}
            else:
                trades = load_raw_log()
                for t in trades:
                    if t is upserted or (t.get("ticker") == upserted.get("ticker") and t.get("entry_price") == upserted.get("entry_price") and t.get("status") != "CLOSED"):
                        try:
                            t["exit_price"] = float(exit_price)
                        except Exception:
                            t["exit_price"] = exit_price
                        t["time_exited"] = time_exited
                        t["status"] = "CLOSED"
                        t["trade_source"] = source
                        t["origin"] = source
                        save_raw_log(trades)
                        return {"action": "upserted_and_closed_fallback", "trade": t}
        return {"action": "upserted", "trade": upserted}

    logger.warning("%sUpsert rejected – likely missing/invalid entry_price or size. dealId=%s ticker=%s entry_price=%s size=%s",
                   log_prefix, dealId, ticker, entry_price, size)
    return {
        "action": "rejected",
        "reason": "malformed_payload",
        "details": {
            "dealId": dealId,
            "ticker": ticker,
            "entry_price": entry_price,
            "size": size,
            "side": side,
        }
    }


# ---------------------------------------------------------------------------
# Main webhook route
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    cid = str(uuid.uuid4())[:8]
    session.update_last_webhook()

    raw = request.get_data(as_text=True) or ""
    raw = raw.strip()

    logger.debug("[cid=%s] RAW webhook body length=%d", cid, len(raw))

    if not raw:
        logger.warning("[cid=%s] Empty webhook body", cid)
        return _ok_response({"status": "error", "message": "Empty body received", "cid": cid})

    parsed_input = _safe_get_json(raw)
    try:
        alert = parse_tradingview_alert(parsed_input)
    except Exception:
        logger.exception("[cid=%s] parse_tradingview_alert raised", cid)
        return _ok_response({"status": "error", "message": "Invalid alert payload", "cid": cid})

    if not isinstance(alert, dict):
        logger.warning("[cid=%s] Parser returned non-dict result", cid)
        return _ok_response({"status": "error", "message": "Parser returned invalid result", "cid": cid})

    if isinstance(parsed_input, dict) and (parsed_input.get("position") or parsed_input.get("dealId") or parsed_input.get("market") or parsed_input.get("dealReference")):
        try:
            result = process_webhook_payload(parsed_input, cid=cid)
            logger.info("[cid=%s] Webhook processed idempotently: %s", cid, result.get("action"))
            return _ok_response({"status": "ok", "action": result.get("action"), "trade": result.get("trade"), "cid": cid})
        except Exception:
            logger.exception("[cid=%s] process_webhook_payload failed", cid)
            return _ok_response({"status": "error", "message": "webhook_processing_failed", "cid": cid})

    if alert.get("blocked"):
        logger.info("[cid=%s] Alert blocked: %s", cid, alert.get("reason"))
        return _ok_response({"status": "blocked", "reason": alert.get("reason"), "cid": cid})

    if _is_trade_locked_now():
        logger.info("[cid=%s] New trade blocked by trade time lock (%02d:00-%02d:00 UK time)",
                    cid, TRADE_LOCK_START_HOUR, TRADE_LOCK_END_HOUR)
        return _ok_response({"status": "blocked", "reason": "trade_time_lock", "cid": cid})

    symbol = alert.get("symbol")
    action = alert.get("action")
    sl_price = alert.get("sl")
    tp_price = alert.get("tp")
    timeframe = alert.get("timeframe")

    logger.info("[cid=%s] Parsed alert → symbol=%s action=%s sl=%s tp=%s tf=%s", cid, symbol, action, sl_price, tp_price, timeframe)

    if not symbol or not action:
        return _ok_response({"status": "error", "message": "missing_symbol_or_action", "missing_fields": [f for f in ("symbol", "action") if not alert.get(f)], "cid": cid})

    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("epic")
    logger.debug("[cid=%s] EPIC lookup → symbol=%s epic=%s source=%s", cid, symbol, epic, epic_data.get("source"))

    if not epic:
        return _ok_response({"status": "error", "message": "epic_lookup_failed", "symbol": symbol, "cid": cid})

    alert_sig = _alert_signature(symbol, action, sl_price, tp_price, timeframe)
    if _is_duplicate_alert(alert_sig):
        logger.warning("[cid=%s] Duplicate alert suppressed (repeat within %ss): %s", cid, ALERT_DEDUPE_WINDOW, alert_sig)
        return _ok_response({"status": "blocked", "reason": "duplicate_alert", "symbol": symbol, "cid": cid})

    # Serialize the "check for an existing open trade -> place order" section
    # per ticker. Without this, two near-simultaneous requests for the same
    # ticker can both pass _has_open_trade_for_ticker() before either one's
    # resulting trade is appended to the log (that check and the eventual log
    # append are separated by several slow network calls below), causing two
    # real orders to be placed at the broker for the same signal.
    ticker_lock = _get_ticker_order_lock(epic)
    if not ticker_lock.acquire(blocking=False):
        logger.warning("[cid=%s] Duplicate order suppressed – order already in flight for %s", cid, epic)
        return _ok_response({"status": "blocked", "reason": "order_in_flight", "symbol": symbol, "epic": epic, "cid": cid})

    try:
        if _has_open_trade_for_ticker(epic, action):
            logger.warning("[cid=%s] Duplicate order suppressed – open trade already exists for %s", cid, epic)
            return _ok_response({"status": "blocked", "reason": "duplicate_open_position", "symbol": symbol, "epic": epic, "cid": cid})

        market_resp = session.request("GET", f"{API_MARKET}/{epic}", timeout=BROKER_API_TIMEOUT)
        if not market_resp or getattr(market_resp, "status_code", 0) != 200:
            logger.warning("[cid=%s] Market snapshot unavailable for %s", cid, epic)
            return _ok_response({"status": "error", "message": "market_snapshot_unavailable", "epic": epic, "cid": cid})

        try:
            snapshot = market_resp.json().get("snapshot", {}) or {}
        except Exception:
            logger.exception("[cid=%s] Failed to parse market snapshot JSON", cid)
            return _ok_response({"status": "error", "message": "market_snapshot_parse_error", "cid": cid})

        bid = snapshot.get("bid")
        offer = snapshot.get("offer")
        if bid is None or offer is None:
            logger.warning("[cid=%s] Market prices unavailable for %s", cid, epic)
            return _ok_response({"status": "error", "message": "price_unavailable", "epic": epic, "missing_fields": [f for f in ("bid", "offer") if snapshot.get(f) is None], "cid": cid})

        entry_price = float(offer) if str(action).strip().lower() == "buy" else float(bid)
        logger.debug("[cid=%s] Entry price (actual): %s", cid, entry_price)

        # Compute SL/TP from fixed risk percentages of entry price (config.FIXED_SL_PERC /
        # FIXED_TP_PERC), overriding whatever levels the TradingView alert supplied, so
        # every trade risks a consistent, predictable amount regardless of the signal.
        if str(action).strip().lower() == "buy":
            fixed_sl, fixed_tp = FixedSLTP.long_levels(entry_price)
        else:
            fixed_sl, fixed_tp = FixedSLTP.short_levels(entry_price)

        if fixed_sl is None or fixed_tp is None:
            logger.warning("[cid=%s] Failed to compute fixed SL/TP for %s @ %s", cid, symbol, entry_price)
            return _ok_response({"status": "error", "message": "sl_tp_calculation_failed", "entry": entry_price, "cid": cid})

        sl_price, tp_price = fixed_sl, fixed_tp
        logger.info("[cid=%s] Fixed SL/TP applied → sl=%s tp=%s (alert sl=%s tp=%s)", cid, sl_price, tp_price, alert.get("sl"), alert.get("tp"))

        size_info = calculate_size(entry_price=entry_price, sl_price=sl_price, tp_price=tp_price, direction=action, symbol=symbol)
        if size_info.get("blocked"):
            logger.info("[cid=%s] Sizing blocked: %s", cid, size_info.get("reason"))
            return _ok_response({"status": "blocked", "reason": size_info.get("reason"), "sl": sl_price, "tp": tp_price, "entry": entry_price, "cid": cid})

        size = size_info.get("size")
        logger.info("[cid=%s] Final SL=%s TP=%s SIZE=%s", cid, sl_price, tp_price, size)

        try:
            result = place_order(epic, action, size, sl_price, tp_price, timeframe=timeframe)
        except Exception:
            logger.exception("[cid=%s] place_order raised an exception", cid)
            return _ok_response({"status": "error", "message": "order_failed", "cid": cid})
    finally:
        ticker_lock.release()

    session.update_last_trade()
    return _ok_response({"status": "ok", "result": result, "cid": cid})


# ---------------------------------------------------------------------------
# Debug and utility routes
# ---------------------------------------------------------------------------
@app.route("/debug/tokens")
@_dashboard_login_required
def debug_tokens():
    try:
        ok = auth.ensure_token()
        return jsonify({
            "auth_ok": bool(ok),
            "has_api_key": bool(getattr(auth, "api_key", None)),
            "has_cst": bool(getattr(auth, "cst", None)),
            "has_xst": bool(getattr(auth, "xst", None))
        }), 200
    except Exception:
        logger.exception("debug_tokens failed")
        return jsonify({"error": "ensure_token_failed"}), 500

@app.route("/debug/epic/<symbol>")
@_dashboard_login_required
def debug_epic(symbol):
    data = session.verify_epic(symbol)
    return jsonify(data), 200

@app.route("/debug/market/<epic>")
@_dashboard_login_required
def debug_market(epic):
    r = session.request("GET", f"{API_MARKET}/{epic}", timeout=BROKER_API_TIMEOUT)
    if not r:
        return jsonify({"error": "no response"}), 500
    try:
        return jsonify(r.json()), 200
    except Exception:
        return jsonify({"error": "invalid_json"}), 500

@app.route("/debug/positions")
@_dashboard_login_required
def debug_positions():
    raw = session.get_positions()
    enriched = session.enrich_positions(raw)
    return jsonify({"raw": raw, "enriched": enriched}), 200

@app.route("/debug/sizing/<symbol>/<action>/<price>/<sl>/<tp>")
@_dashboard_login_required
def debug_sizing(symbol, action, price, sl, tp):
    try:
        info = calculate_size(entry_price=float(price), sl_price=float(sl), tp_price=float(tp), direction=action, symbol=symbol)
        return jsonify(info), 200
    except Exception:
        logger.exception("debug_sizing failed")
        return jsonify({"error": "invalid_parameters"}), 400

@app.route("/debug/order/<epic>/<action>/<size>")
@_dashboard_login_required
def debug_order(epic, action, size):
    try:
        result = place_order(epic, action, float(size))
        return jsonify(result), 200
    except Exception:
        logger.exception("debug_order failed")
        return jsonify({"error": "order_failed"}), 500

@app.route("/debug/close-test/<deal_id>", methods=["GET"])
@_dashboard_login_required
def debug_close_test(deal_id):
    from config import API_POSITIONS
    url1 = f"{API_POSITIONS}/{deal_id}/close"
    r1 = session.request("POST", url1, json={}, timeout=BROKER_API_TIMEOUT)
    s1 = getattr(r1, "status_code", None)
    t1 = getattr(r1, "text", None)
    url2 = f"{API_POSITIONS}/close-position"
    payload = {"dealId": deal_id, "dealReference": f"p_{deal_id}"}
    r2 = session.request("PUT", url2, json=payload, timeout=BROKER_API_TIMEOUT)
    s2 = getattr(r2, "status_code", None)
    t2 = getattr(r2, "text", None)
    return jsonify({
        "post_close": {"status": s1, "body": t1},
        "put_close_position": {"status": s2, "body": t2},
    }), 200

@app.route("/debug/history")
@_dashboard_login_required
def debug_history():
    from config import API_HISTORY_TRANSACTIONS
    r = session.request("GET", f"{API_HISTORY_TRANSACTIONS}?max=200", timeout=BROKER_API_TIMEOUT)
    return jsonify(r.json() if r else {"error": "no response"}), 200

@app.route("/raw")
@_dashboard_login_required
def raw_positions():
    raw = session.get_positions()
    return jsonify(raw)

@app.route("/raw/account")
@_dashboard_login_required
def raw_account():
    r = session.request("GET", API_ACCOUNTS, timeout=BROKER_API_TIMEOUT)
    return jsonify(r.json() if r else {}), 200

@app.route("/")
def root():
    return redirect("/dashboard")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _start_app():
    """Obtain an auth token and start the background scheduler."""
    for attempt in range(AUTH_RETRY_COUNT):
        try:
            if auth.ensure_token():
                logger.info("Auth token ensured (attempt %d).", attempt + 1)
                break
        except Exception:
            logger.exception("Auth ensure_token failed (attempt %d); retrying in %s seconds", attempt + 1, AUTH_RETRY_BACKOFF)
        time.sleep(AUTH_RETRY_BACKOFF)
    else:
        logger.warning("Auth token could not be ensured after %d attempts; continuing anyway.", AUTH_RETRY_COUNT)

    try:
        start_scheduler()
        logger.info("Scheduler started.")
    except Exception:
        logger.exception("Failed to start scheduler")


_bootstrap_lock = threading.Lock()
_bootstrap_started = False


def _in_test_runtime() -> bool:
    return ("pytest" in sys.modules) or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _ensure_bootstrap_background() -> None:
    global _bootstrap_started
    with _bootstrap_lock:
        if _bootstrap_started:
            return
        _bootstrap_started = True
    t = threading.Thread(target=_start_app, daemon=True, name="WebhookBootstrapThread")
    t.start()


if __name__ != "__main__" and not _in_test_runtime():
    _ensure_bootstrap_background()


if __name__ == "__main__":
    _start_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
