# ============================
# WEBHOOK MODULE (REVERTED + DEBUG SAFE, CLEANED)
# ============================

from flask import Flask, request, jsonify, render_template, redirect
import json
import time
import os

import session
from parser import parse_tradingview_alert
from sizing import calculate_size
from order import place_order
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET
from dashboard import dashboard as dashboard_blueprint
from scheduler import start_scheduler
from trade_log import load_raw_log
from close_position import close_position as close_position_module

app = Flask(__name__)
app.config["DEBUG"] = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}
app.url_map.strict_slashes = False

app.register_blueprint(dashboard_blueprint)


# ============================
# MAIN WEBHOOK
# ============================

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw = request.get_data(as_text=True)
    raw = raw.strip() if raw else ""

    print("[WEBHOOK] RAW:", raw, flush=True)

    if not raw:
        return jsonify({"status": "error", "message": "Empty body received"}), 200

    alert = None

    # -----------------------------
    # Parse TradingView alert
    # -----------------------------
    try:
        data = request.get_json(force=True)
        alert = parse_tradingview_alert(data)
    except Exception as e:
        print("[WEBHOOK] JSON parse failed, falling back to raw:", e, flush=True)
        try:
            data = json.loads(raw)
            alert = parse_tradingview_alert(data)
        except Exception as e2:
            print("[WEBHOOK] json.loads fallback failed, using raw string:", e2, flush=True)
            try:
                alert = parse_tradingview_alert(raw)
            except Exception as e3:
                print("[WEBHOOK] PARSE ERROR:", e3, flush=True)
                return jsonify({"status": "error", "message": "Invalid alert"}), 200

    symbol = alert["symbol"]
    action = alert["action"]
    sl_price = alert.get("sl")
    tp_price = alert.get("tp")
    timeframe = alert.get("timeframe")

    print(
        f"[WEBHOOK] Parsed alert → symbol={symbol}, action={action}, SL={sl_price}, "
        f"TP={tp_price}, TF={timeframe}",
        flush=True,
    )

    # -----------------------------
    # EPIC lookup (reverted)
    # -----------------------------
    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("epic")

    print(
        f"[WEBHOOK] EPIC lookup → symbol={symbol}, epic={epic}, "
        f"source={epic_data.get('source')}",
        flush=True,
    )

    if not epic:
        print("[WEBHOOK] EPIC lookup failed:", symbol, flush=True)
        return jsonify({"status": "error", "message": "epic_lookup_failed"}), 200

    # -----------------------------
    # Market snapshot
    # -----------------------------
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print("[WEBHOOK] Market snapshot unavailable for:", epic, flush=True)
        return jsonify({"status": "error", "message": "market_snapshot_unavailable"}), 200

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print("[WEBHOOK] Market prices unavailable for:", epic, flush=True)
        return jsonify({"status": "error", "message": "price_unavailable"}), 200

    entry_price = offer if action.lower() == "buy" else bid

    print(f"[WEBHOOK] Entry price (actual): {entry_price}", flush=True)

    # -----------------------------
    # Sizing
    # -----------------------------
    size_info = calculate_size(
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        direction=action,
    )

    if size_info["blocked"]:
        print(f"[WEBHOOK] SIZING BLOCKED: {size_info['reason']}", flush=True)
        return jsonify({"status": "blocked", "reason": size_info["reason"]}), 200

    size = size_info["size"]

    print("[WEBHOOK] Final SL:", sl_price, flush=True)
    print("[WEBHOOK] Final TP:", tp_price, flush=True)
    print("[WEBHOOK] Final SIZE:", size, flush=True)

    # -----------------------------
    # Place order (reverted pipeline)
    # -----------------------------
    result = place_order(epic, action, size, sl_price, tp_price, timeframe=timeframe)

    session.update_last_trade()

    return jsonify({"status": "ok", "result": result}), 200


# ============================
# DEBUG ROUTES
# ============================

@app.route("/debug/tokens")
def debug_tokens():
    try:
        auth.ensure_token()
        return jsonify({
            "api_key": auth.api_key,
            "cst": auth.cst,
            "xst": auth.xst
        }), 200
    except Exception as e:
        print(f"[DEBUG] ensure_token failed: {e}", flush=True)
        return jsonify({"error": "ensure_token_failed", "details": str(e)}), 500


@app.route("/debug/epic/<symbol>")
def debug_epic(symbol):
    data = session.verify_epic(symbol)
    return jsonify(data), 200


@app.route("/debug/market/<epic>")
def debug_market(epic):
    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r:
        return jsonify({"error": "no response"}), 500
    return jsonify(r.json()), 200


@app.route("/debug/positions")
def debug_positions():
    raw = session.get_positions()
    enriched = session.enrich_positions(raw)
    return jsonify({"raw": raw, "enriched": enriched}), 200


@app.route("/debug/sizing/<symbol>/<action>/<price>/<sl>/<tp>")
def debug_sizing(symbol, action, price, sl, tp):
    info = calculate_size(
        entry_price=float(price),
        sl_price=float(sl),
        tp_price=float(tp),
        direction=action
    )
    return jsonify(info), 200


@app.route("/debug/order/<epic>/<action>/<size>")
def debug_order(epic, action, size):
    result = place_order(epic, action, float(size))
    return jsonify(result), 200


# ============================
# RAW + DASHBOARD
# ============================

@app.route("/raw")
def raw_positions():
    # If fetch_positions_from doesn't exist in your current session.py,
    # switch this to session.get_positions()
    return jsonify(session.fetch_positions_from(API_POSITIONS))


@app.route("/raw/account")
def raw_account():
    r = session.request("GET", API_ACCOUNTS)
    return jsonify(r.json() if r else {})


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
    # If get_daily_report doesn't exist, you can safely set daily_report = {}
    daily_report = session.get_daily_report() or {}

    return render_template(
        "dashboard.html",
        title="AG Capital Trader",
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
    result = close_position_module(position_id)
    return jsonify(result), 200


# ============================
# BOOTSTRAP
# ============================

if __name__ == "__main__":
    while True:
        try:
            auth.ensure_token()
            print("[Webhook] Auth token ensured.", flush=True)
            break
        except Exception as e:
            print(f"[Webhook] Auth ensure_token failed: {e}", flush=True)
            time.sleep(10)

    print("[Webhook] Starting scheduler...", flush=True)
    start_scheduler()
    print("[Webhook] Scheduler started.", flush=True)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
