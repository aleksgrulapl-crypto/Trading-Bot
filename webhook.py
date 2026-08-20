# webhook.py
# ============================
# WEBHOOK MODULE (PATCHED: idempotent webhook processing + original TradingView flow)
# ============================

import os
import time
import json
import logging
from typing import Any, Dict, Optional

from flask import Flask, request, jsonify, render_template, redirect

import session
from sizing import calculate_size
from order import place_order
from auth import auth
from config import API_ACCOUNTS, API_MARKET, API_BASE, DEBUG_LOGS
from scheduler import start_scheduler
from trade_log import load_raw_log, save_raw_log, append_open_trade, set_dealId_for_dealReference, close_trade_by_dealId
from close_position import close_position as close_position_module

# parser import: try both common names for compatibility
try:
    from tradingview_parser import parse_tradingview_alert
except Exception:
    try:
        from parser import parse_tradingview_alert
    except Exception:
        # fallback stub that blocks everything
        def parse_tradingview_alert(_):
            return {"blocked": True, "reason": "parser_unavailable"}

# dashboard blueprint import (may raise if not present)
try:
    from dashboard import dashboard as dashboard_blueprint
except Exception:
    dashboard_blueprint = None

# App setup
app = Flask(__name__)
app.config["DEBUG"] = bool(DEBUG_LOGS)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}

# Register blueprint if available
if dashboard_blueprint:
    app.register_blueprint(dashboard_blueprint)

# Logging
logger = logging.getLogger("webhook")
logger.setLevel(logging.DEBUG if DEBUG_LOGS else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [webhook] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)


# -----------------------------
# Helpers
# -----------------------------
def _safe_get_json(raw_text: str) -> Any:
    """
    Try multiple strategies to parse incoming payload:
      1) request.get_json(force=True)
      2) json.loads(raw_text)
      3) fallback to raw string
    """
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


# -----------------------------
# Idempotent webhook processing helpers
# -----------------------------
def _make_signature(dealId: Optional[str], dealReference: Optional[str], ticker: Optional[str], entry_price: Optional[float]) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"


def _find_existing_entry(trades: list, dealId: Optional[str], dealReference: Optional[str], ticker: Optional[str], entry_price: Optional[float]):
    # 1) match by dealId
    if dealId:
        for t in trades:
            if t.get("dealId") and str(t.get("dealId")) == str(dealId):
                return t, "dealId"
    # 2) match by dealReference
    if dealReference:
        for t in trades:
            if t.get("dealReference") and str(t.get("dealReference")) == str(dealReference):
                return t, "dealReference"
    # 3) match by signature (dealId|dealReference|ticker|entry_price)
    sig = _make_signature(dealId, dealReference, ticker, entry_price)
    for t in trades:
        existing_sig = _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price"))
        if existing_sig == sig:
            return t, "signature"
    return None, None


def process_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Idempotent processing of incoming webhook payloads.
    - payload: raw JSON from webhook provider (Capital or similar) or other webhook sources
    Returns a dict with action taken and the trade record.
    """
    # Normalize fields from common webhook shapes
    pos = payload.get("position") or payload
    market = payload.get("market") or payload

    dealId = pos.get("dealId") or pos.get("id") or payload.get("dealId")
    dealReference = pos.get("dealReference") or payload.get("dealReference")
    ticker = market.get("symbol") or market.get("epic") or pos.get("instrument") or payload.get("ticker")
    side = pos.get("direction") or payload.get("side")
    size = pos.get("size") or pos.get("contractSize") or payload.get("size")
    entry_price = pos.get("level") or pos.get("entryPrice") or payload.get("entry_price") or payload.get("entryPrice")
    exit_price = payload.get("price") or payload.get("exit_price") or payload.get("closePrice")
    time_entered = pos.get("createdDate") or pos.get("createdDateUTC") or payload.get("time_entered")
    time_exited = payload.get("closedDate") or payload.get("time_exited") or payload.get("timestamp")

    # Load current log
    trades = load_raw_log()

    # Find existing entry if any
    existing, how = _find_existing_entry(trades, dealId, dealReference, ticker, entry_price)

    # If we have an existing entry, update it (do not append)
    if existing:
        updated = False
        # map fields that may be missing and should be filled/updated
        if not existing.get("dealId") and dealId:
            existing["dealId"] = dealId
            updated = True
        if not existing.get("dealReference") and dealReference:
            existing["dealReference"] = dealReference
            updated = True
        if not existing.get("ticker") and ticker:
            existing["ticker"] = ticker
            updated = True
        if not existing.get("side") and side:
            existing["side"] = side
            updated = True
        if (existing.get("size") in (None, 0)) and size:
            try:
                existing["size"] = float(size)
            except Exception:
                existing["size"] = size
            updated = True
        if (existing.get("entry_price") in (None, "")) and entry_price not in (None, ""):
            try:
                existing["entry_price"] = float(entry_price)
            except Exception:
                existing["entry_price"] = entry_price
            updated = True
        # If webhook contains an exit, close the trade
        if exit_price not in (None, ""):
            # prefer closing by dealId if present
            if existing.get("dealId"):
                close_trade_by_dealId(existing.get("dealId"), exit_price=exit_price, time_exited=time_exited, note="Closed via webhook")
            else:
                # set exit fields directly and compute pnl via trade_log save
                try:
                    existing["exit_price"] = float(exit_price)
                except Exception:
                    existing["exit_price"] = exit_price
                existing["time_exited"] = time_exited
                existing["status"] = "CLOSED"
            updated = True

        if updated:
            # persist changes
            save_raw_log(trades)
            return {"action": "updated", "match_by": how, "trade": existing}

        return {"action": "noop", "match_by": how, "trade": existing}

    # No existing entry found -> append new open/closed trade
    new = {
        "dealId": dealId,
        "dealReference": dealReference,
        "ticker": ticker,
        "side": side,
        "size": float(size) if size not in (None, "") else 0,
        "entry_price": float(entry_price) if entry_price not in (None, "") else None,
        "time_entered": time_entered,
        "exit_price": float(exit_price) if exit_price not in (None, "") else None,
        "time_exited": time_exited,
        "status": "CLOSED" if exit_price not in (None, "") else "OPEN",
        "notes": "Imported from webhook"
    }

    appended = append_open_trade(new)
    if appended:
        return {"action": "appended", "trade": appended}
    # fallback: if append failed, write directly to file (last resort)
    trades.append(new)
    save_raw_log(trades)
    return {"action": "appended_fallback", "trade": new}


# ============================
# MAIN WEBHOOK
# ============================
@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw = request.get_data(as_text=True) or ""
    raw = raw.strip()

    logger.debug("RAW webhook body length=%d", len(raw))

    if not raw:
        logger.warning("Empty webhook body")
        return _ok_response({"status": "error", "message": "Empty body received"})

    # Parse TradingView alert (robust)
    parsed_input = _safe_get_json(raw)
    try:
        alert = parse_tradingview_alert(parsed_input)
    except Exception as e:
        logger.exception("parse_tradingview_alert raised")
        return _ok_response({"status": "error", "message": "Invalid alert payload"})

    if not isinstance(alert, dict):
        logger.warning("Parser returned non-dict result")
        return _ok_response({"status": "error", "message": "Parser returned invalid result"})

    # If the parsed alert looks like a broker position/webhook (contains 'position' or 'dealId' or 'market'),
    # handle it idempotently via process_webhook_payload and return early.
    if isinstance(parsed_input, dict) and (parsed_input.get("position") or parsed_input.get("dealId") or parsed_input.get("market") or parsed_input.get("dealReference")):
        try:
            result = process_webhook_payload(parsed_input)
            logger.info("Webhook processed idempotently: %s", result.get("action"))
            return _ok_response({"status": "ok", "action": result.get("action"), "trade": result.get("trade")})
        except Exception:
            logger.exception("process_webhook_payload failed")
            return _ok_response({"status": "error", "message": "webhook_processing_failed"})

    # Otherwise treat as TradingView alert for order placement
    if alert.get("blocked"):
        logger.info("Alert blocked: %s", alert.get("reason"))
        return _ok_response({"status": "blocked", "reason": alert.get("reason")})

    symbol = alert.get("symbol")
    action = alert.get("action")
    sl_price = alert.get("sl")
    tp_price = alert.get("tp")
    timeframe = alert.get("timeframe")

    logger.info("Parsed alert → symbol=%s action=%s sl=%s tp=%s tf=%s", symbol, action, sl_price, tp_price, timeframe)

    if not symbol or not action:
        return _ok_response({"status": "error", "message": "missing_symbol_or_action"})

    # EPIC lookup
    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("epic")
    logger.debug("EPIC lookup → symbol=%s epic=%s source=%s", symbol, epic, epic_data.get("source"))

    if not epic:
        return _ok_response({"status": "error", "message": "epic_lookup_failed"})

    # Market snapshot
    market_resp = session.request("GET", f"{API_MARKET}/{epic}")
    if not market_resp or getattr(market_resp, "status_code", 0) != 200:
        logger.warning("Market snapshot unavailable for %s", epic)
        return _ok_response({"status": "error", "message": "market_snapshot_unavailable"})

    try:
        snapshot = market_resp.json().get("snapshot", {}) or {}
    except Exception:
        logger.exception("Failed to parse market snapshot JSON")
        return _ok_response({"status": "error", "message": "market_snapshot_parse_error"})

    bid = snapshot.get("bid")
    offer = snapshot.get("offer")
    if bid is None or offer is None:
        logger.warning("Market prices unavailable for %s", epic)
        return _ok_response({"status": "error", "message": "price_unavailable"})

    entry_price = float(offer) if str(action).strip().lower() == "buy" else float(bid)
    logger.debug("Entry price (actual): %s", entry_price)

    # Sizing
    size_info = calculate_size(entry_price=entry_price, sl_price=sl_price, tp_price=tp_price, direction=action, symbol=symbol)
    if size_info.get("blocked"):
        logger.info("Sizing blocked: %s", size_info.get("reason"))
        return _ok_response({"status": "blocked", "reason": size_info.get("reason")})

    size = size_info.get("size")
    logger.info("Final SL=%s TP=%s SIZE=%s", sl_price, tp_price, size)

    # Place order
    try:
        result = place_order(epic, action, size, sl_price, tp_price, timeframe=timeframe)
    except Exception:
        logger.exception("place_order raised an exception")
        return _ok_response({"status": "error", "message": "order_failed"})

    session.update_last_trade()
    return _ok_response({"status": "ok", "result": result})


# ============================
# DEBUG ROUTES (SAFE)
# ============================
@app.route("/debug/tokens")
def debug_tokens():
    """
    Safe debug: indicate whether tokens are present, do not return secrets.
    """
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
def debug_epic(symbol):
    data = session.verify_epic(symbol)
    return jsonify(data), 200


@app.route("/debug/market/<epic>")
def debug_market(epic):
    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r:
        return jsonify({"error": "no response"}), 500
    try:
        return jsonify(r.json()), 200
    except Exception:
        return jsonify({"error": "invalid_json"}), 500


@app.route("/debug/positions")
def debug_positions():
    raw = session.get_positions()
    enriched = session.enrich_positions(raw)
    return jsonify({"raw": raw, "enriched": enriched}), 200


@app.route("/debug/sizing/<symbol>/<action>/<price>/<sl>/<tp>")
def debug_sizing(symbol, action, price, sl, tp):
    try:
        info = calculate_size(entry_price=float(price), sl_price=float(sl), tp_price=float(tp), direction=action, symbol=symbol)
        return jsonify(info), 200
    except Exception:
        logger.exception("debug_sizing failed")
        return jsonify({"error": "invalid_parameters"}), 400


@app.route("/debug/order/<epic>/<action>/<size>")
def debug_order(epic, action, size):
    try:
        result = place_order(epic, action, float(size))
        return jsonify(result), 200
    except Exception:
        logger.exception("debug_order failed")
        return jsonify({"error": "order_failed"}), 500


@app.route("/debug/close-test/<deal_id>", methods=["GET"])
def debug_close_test(deal_id):
    """
    Test close endpoints: returns raw responses for inspection.
    """
    from config import API_POSITIONS

    url1 = f"{API_POSITIONS}/{deal_id}/close"
    r1 = session.request("POST", url1, json={})
    s1 = getattr(r1, "status_code", None)
    t1 = getattr(r1, "text", None)

    url2 = f"{API_POSITIONS}/close-position"
    payload = {"dealId": deal_id, "dealReference": f"p_{deal_id}"}
    r2 = session.request("PUT", url2, json=payload)
    s2 = getattr(r2, "status_code", None)
    t2 = getattr(r2, "text", None)

    return jsonify({
        "post_close": {"status": s1, "body": t1},
        "put_close_position": {"status": s2, "body": t2},
    }), 200


@app.route("/debug/history")
def debug_history():
    from config import API_HISTORY_TRANSACTIONS
    r = session.request("GET", f"{API_HISTORY_TRANSACTIONS}?max=200")
    return jsonify(r.json() if r else {"error": "no response"}), 200


# ============================
# RAW + DASHBOARD ROUTES
# ============================
@app.route("/raw")
def raw_positions():
    raw = session.get_positions()
    return jsonify(raw)


@app.route("/raw/account")
def raw_account():
    r = session.request("GET", API_ACCOUNTS)
    return jsonify(r.json() if r else {}), 200


@app.route("/")
def root():
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard_home():
    raw_positions = session.get_positions() or []
    positions = session.enrich_positions(raw_positions)

    raw_account = session.get_account() or {}
    account = session.enrich_account(raw_account)

    trade_log = load_raw_log()
    daily_report = session.get_daily_report() or {}

    return render_template(
        "dashboard.html",
        title=os.getenv("DASHBOARD_TITLE", "AG Capital Trader"),
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trades=trade_log,
        analytics=session.shared_state.get("analytics", {}),
        system_status=session.shared_state.get("system_status", {}),
        daily_report=daily_report,
    )


@app.route("/close/<position_id>", methods=["POST"])
def close_position_route(position_id):
    logger.info("Dashboard close requested for position_id=%s", position_id)
    result = close_position_module(position_id)
    return jsonify(result), 200


# ============================
# BOOTSTRAP
# ============================
def _start_app():
    # Ensure auth token before starting scheduler
    backoff = 5
    for _ in range(6):
        try:
            if auth.ensure_token():
                logger.info("Auth token ensured.")
                break
        except Exception:
            logger.exception("Auth ensure_token failed; retrying in %s seconds", backoff)
        time.sleep(backoff)
    else:
        logger.warning("Auth token could not be ensured before startup; continuing anyway.")

    # Start scheduler (idempotent)
    try:
        start_scheduler()
        logger.info("Scheduler started.")
    except Exception:
        logger.exception("Failed to start scheduler")


if __name__ == "__main__":
    _start_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
