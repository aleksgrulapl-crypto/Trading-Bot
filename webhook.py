from flask import Flask, request, jsonify, render_template
from scheduler import start_scheduler
from dashboard import dashboard as dashboard_blueprint

import json
import time
import session
import order
from trade_log import load_log
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, EQUITY_PERCENT
from parser import parse_tradingview_alert
from sizing import calculate_size

app = Flask(__name__)
app.register_blueprint(dashboard_blueprint)

print("[Webhook] Starting scheduler...")
start_scheduler()
print("[Webhook] Scheduler started.")

# ---------------------------------------------------------
# RAW DEBUG ENDPOINTS
# ---------------------------------------------------------

@app.route("/raw")
def raw_positions():
    return jsonify(session.fetch_positions_from(API_POSITIONS))

@app.route("/raw/account")
def raw_account():
    return jsonify(session.request("GET", API_ACCOUNTS).json())

# ---------------------------------------------------------
# WEBHOOK ENDPOINT
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    # Always normalize raw input
    raw = request.get_data(as_text=True)
    raw = raw.strip().replace("\n", "").replace("\r", "")

    print("[WEBHOOK] RAW:", raw)

    if not raw:
        return jsonify({"status": "error", "message": "Empty body received"}), 200

    # Try JSON first
    try:
        data = request.get_json(force=True)
        alert = parse_tradingview_alert(data)
    except:
        # Fallback: treat as raw string
        try:
            alert = parse_tradingview_alert(raw)
        except Exception as e:
            print("[WEBHOOK] PARSE ERROR:", e)
            return jsonify({"status": "error", "message": "Invalid alert"}), 200

    ticker = alert["symbol"]
    action = alert["action"]

    # EPIC lookup
    epic_data = session.verify_epic(ticker)
    epic = epic_data.get("epic")

    if not epic:
        return jsonify({"status": "error", "message": "EPIC lookup failed"}), 200

    # Get entry price from market data
    entry_price = session.get_market_price(epic)
    if not entry_price:
        return jsonify({"status": "error", "message": "Entry price unavailable"}), 200

    # Get available balance
    account = session.get_account()
    available = account.get("available", 0)

    # SL/TP from alert
    sl_price = alert.get("sl")
    tp_price = alert.get("tp")

    # Sizing
    size_info = calculate_size(
        available=available,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        direction=action
    )

    if size_info["blocked"]:
        print(f"[ORDER] Skipped due to sizing block: {size_info['reason']}")
        return jsonify({"status": "blocked", "reason": size_info["reason"]}), 200

    size = size_info["size"]

    print("[WEBHOOK] Final SL:", sl_price)
    print("[WEBHOOK] Final TP:", tp_price)

    # Place order
    result = order.place_order(epic, action, size, sl_price, tp_price)

    session.update_last_trade()

    return jsonify({"status": "ok", "result": result}), 200

# ---------------------------------------------------------
# DASHBOARD ROUTE
# ---------------------------------------------------------

@app.route("/")
@app.route("/dashboard")
def dashboard():
    raw_positions = session.get_positions() or []
    positions = session.enrich_positions(raw_positions)

    raw_account = session.get_account() or {}
    account = session.enrich_account(raw_account)

    trade_log = load_log()
    daily_report = session.get_daily_report() or {}

    return render_template(
        "dashboard.html",
        title="AG Capital Trader",
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trade_log=trade_log,
        daily_report=daily_report,
        system_status=session.shared_state.get("system_status", {})
    )

# ---------------------------------------------------------
# CLOSE POSITION
# ---------------------------------------------------------

@app.route("/close/<position_id>", methods=["POST"])
def close_position(position_id):
    auth.ensure_token()
    payload = {"dealId": position_id, "direction": "SELL", "size": 0}
    r = session.request("POST", f"{API_POSITIONS}/{position_id}/close", json=payload)
    result = r.json()
    session.update_last_trade()
    return jsonify({"status": "ok", "result": result})

# ---------------------------------------------------------
# RUN APP
# ---------------------------------------------------------

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
