from flask import Flask, request, jsonify, render_template
import json
import time

import session
from parser import parse_tradingview_alert
from sizing import calculate_size
from order import place_order
from trade_log import load_log
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET
from dashboard import dashboard as dashboard_blueprint
from scheduler import start_scheduler

app = Flask(__name__)
app.url_map.strict_slashes = False

# ---------------------------------------------------------
# WEBHOOK ENDPOINT (REGISTER FIRST — CRITICAL)
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw = request.get_data(as_text=True)
    raw = raw.strip() if raw else ""

    print("[WEBHOOK] RAW:", raw)

    if not raw:
        return jsonify({"status": "error", "message": "Empty body received"}), 200

    # ---------------------------------------------------------
    # PARSE ALERT (supports raw + JSON)
    # ---------------------------------------------------------
    try:
        data = request.get_json(force=True)
        alert = parse_tradingview_alert(data)
    except:
        try:
            data = json.loads(raw)
            alert = parse_tradingview_alert(data)
        except:
            try:
                alert = parse_tradingview_alert(raw)
            except Exception as e:
                print("[WEBHOOK] PARSE ERROR:", e)
                return jsonify({"status": "error", "message": "Invalid alert"}), 200

    symbol = alert["symbol"]
    action = alert["action"]
    sl_price = alert.get("sl")
    tp_price = alert.get("tp")

    # ---------------------------------------------------------
    # EPIC LOOKUP
    # ---------------------------------------------------------
    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("epic")

    if not epic:
        print("[WEBHOOK] EPIC lookup failed:", symbol)
        return jsonify({"status": "error", "message": "EPIC lookup failed"}), 200

    # ---------------------------------------------------------
    # MARKET SNAPSHOT (FIXED URL)
    # ---------------------------------------------------------
    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print("[WEBHOOK] Market snapshot unavailable for:", epic)
        return jsonify({"status": "error", "message": "Market snapshot unavailable"}), 200

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print("[WEBHOOK] Market prices unavailable for:", epic)
        return jsonify({"status": "error", "message": "Price unavailable"}), 200

    entry_price = (bid + offer) / 2

    # ---------------------------------------------------------
    # ACCOUNT BALANCE (correct cash balance)
    # ---------------------------------------------------------
    account = session.get_account()
    cash_balance = account.get("balance", {}).get("balance", 0)

    # ---------------------------------------------------------
    # SIZING
    # ---------------------------------------------------------
    size_info = calculate_size(
        available=cash_balance,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        direction=action
    )

    if size_info["blocked"]:
        print(f"[WEBHOOK] SIZING BLOCKED: {size_info['reason']}")
        return jsonify({"status": "blocked", "reason": size_info["reason"]}), 200

    size = size_info["size"]

    print("[WEBHOOK] Final SL:", sl_price)
    print("[WEBHOOK] Final TP:", tp_price)
    print("[WEBHOOK] Final SIZE:", size)

    # ---------------------------------------------------------
    # PLACE ORDER
    # ---------------------------------------------------------
    result = place_order(epic, action, size, sl_price, tp_price)

    session.update_last_trade()

    return jsonify({"status": "ok", "result": result}), 200


# ---------------------------------------------------------
# REGISTER DASHBOARD AFTER WEBHOOK
# ---------------------------------------------------------

app.register_blueprint(dashboard_blueprint)

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
# START SCHEDULER + RUN APP
# ---------------------------------------------------------

print("[Webhook] Starting scheduler...")
start_scheduler()
print("[Webhook] Scheduler started.")

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
