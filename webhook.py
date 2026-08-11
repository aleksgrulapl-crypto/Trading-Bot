from flask import Flask, request, jsonify, render_template
from scheduler import start_scheduler
from dashboard import dashboard as dashboard_blueprint
import session
import order
import utils
import report
from trade_log import load_log
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS

# NEW IMPORT
from parser import parse_tradingview_alert

app = Flask(__name__)
app.register_blueprint(dashboard_blueprint)


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

print("[Webhook] Starting scheduler...")
start_scheduler()
print("[Webhook] Scheduler started.")


# ---------------------------------------------------------
# RAW DEBUG ENDPOINTS
# ---------------------------------------------------------

@app.route("/raw")
def raw_positions():
    raw = session.fetch_positions_from(API_POSITIONS)
    return jsonify(raw)

@app.route("/raw/account")
def raw_account():
    raw = session.request("GET", API_ACCOUNTS).json()
    return jsonify(raw)


# ---------------------------------------------------------
# WEBHOOK ENDPOINT
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    # Read raw body directly from the WSGI stream BEFORE Flask parses anything
    try:
        raw = request.environ['wsgi.input'].read().decode('utf-8', errors='ignore')
    except Exception as e:
        raw = f"[ERROR reading raw body: {e}]"

    print("\n" + "="*60)
    print("[Webhook] RAW BODY RECEIVED:")
    print(raw)
    print("="*60 + "\n")

    # If raw body is empty, return debug info
    if not raw.strip():
        return jsonify({
            "status": "error",
            "message": "TradingView sent an empty body",
            "debug": "Render may be stripping the body"
        }), 200  # <-- IMPORTANT: return 200 so Render does NOT strip the body next time

    # Extract JSON block
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        json_text = raw[start:end]
        data = json.loads(json_text)
    except Exception as e:
        print("[Webhook] JSON PARSE ERROR:", e)
        return jsonify({"status": "error", "message": "Invalid JSON"}), 200

    try:
        alert = parse_tradingview_alert(data)

        symbol = alert["symbol"]
        action = alert["action"]
        size = alert["quantity"]
        sl = alert["sl"]
        tp = alert["tp"]

        print(f"[Webhook] Parsed alert: {symbol} {action} {size} SL={sl} TP={tp}")

        result = order.place_order(symbol, action, size, sl, tp)
        return jsonify({"status": "ok", "result": result}), 200

    except Exception as e:
        print("[Webhook] ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 200


# ---------------------------------------------------------
# DASHBOARD ROUTES
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

    payload = {
        "dealId": position_id,
        "direction": "SELL",
        "size": 0
    }

    r = session.request("POST", f"{API_POSITIONS}/{position_id}/close", json=payload)
    result = r.json()

    for p in session.shared_state.get("positions", []):
        if str(p["id"]) == str(position_id):
            utils.log_trade(
                ticker=p["ticker"],
                side="CLOSE",
                size=p["size"],
                price=p["current_price"],
                pnl=p["profit"],
                timestamp=utils.timestamp()
            )

    session.update_last_trade()
    return jsonify({"status": "ok", "result": result})


# ---------------------------------------------------------
# MANUAL DAILY REPORT
# ---------------------------------------------------------

@app.route("/daily-report", methods=["POST"])
def manual_daily_report():
    report_data = report.generate_daily_report()
    return jsonify(report_data)


# ---------------------------------------------------------
# RUN APP
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
