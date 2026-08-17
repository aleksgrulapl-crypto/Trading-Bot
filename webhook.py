# ============================
# WEBHOOK MODULE (SL/TP + CLEAN TRADE LOGGING)
# ============================

from flask import Flask, request, jsonify, render_template, redirect
import json
import time
import os

import session
from parser import parse_tradingview_alert
from sizing import calculate_size
from order import place_order
from trade_log import log_trade
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS, API_MARKET
from dashboard import dashboard as dashboard_blueprint
from scheduler import start_scheduler
from utils import timestamp
from close_position import close_position as close_position_module

app = Flask(__name__)

app.config["DEBUG"] = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}
app.url_map.strict_slashes = False


@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw = request.get_data(as_text=True)
    raw = raw.strip() if raw else ""

    print("[WEBHOOK] RAW:", raw, flush=True)

    if not raw:
        return jsonify({"status": "error", "message": "Empty body received"}), 200

    alert = None

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

    if alert.get("blocked"):
        print(f"[WEBHOOK] ALERT BLOCKED: {alert.get('reason')}", flush=True)

        log_trade(
            ticker=alert.get("symbol") or "UNKNOWN",
            epic=None,
            deal_id=None,
            side="BLOCKED",
            size=0,
            price=0,
            sl=alert.get("sl"),
            tp=alert.get("tp"),
            timestamp=timestamp(),
            timeframe=alert.get("timeframe"),
        )

        return jsonify({"status": "blocked", "reason": alert.get("reason")}), 200

    symbol = alert["symbol"]
    action = alert["action"]
    sl_price = alert["sl"]
    tp_price = alert["tp"]
    timeframe = alert.get("timeframe")

    print(
        f"[WEBHOOK] Parsed alert → symbol={symbol}, action={action}, SL={sl_price}, TP={tp_price}, TF={timeframe}",
        flush=True,
    )

    epic_data = session.verify_epic(symbol)
    epic = epic_data.get("epic")

    print(
        f"[WEBHOOK] EPIC lookup → symbol={symbol}, epic={epic}, source={epic_data.get('source')}",
        flush=True,
    )

    if not epic:
        print("[WEBHOOK] EPIC lookup failed:", symbol, flush=True)

        log_trade(
            ticker=symbol,
            epic=None,
            deal_id=None,
            side="BLOCKED",
            size=0,
            price=0,
            sl=sl_price,
            tp=tp_price,
            timestamp=timestamp(),
            timeframe=timeframe,
        )

        return jsonify({"status": "blocked", "reason": "epic_lookup_failed"}), 200

    market = session.request("GET", f"{API_MARKET}/{epic}")
    if not market or market.status_code != 200:
        print("[WEBHOOK] Market snapshot unavailable for:", epic, flush=True)

        log_trade(
            ticker=symbol,
            epic=epic,
            deal_id=None,
            side="BLOCKED",
            size=0,
            price=0,
            sl=sl_price,
            tp=tp_price,
            timestamp=timestamp(),
            timeframe=timeframe,
        )

        return jsonify({"status": "blocked", "reason": "market_snapshot_unavailable"}), 200

    snapshot = market.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print("[WEBHOOK] Market prices unavailable for:", epic, flush=True)

        log_trade(
            ticker=symbol,
            epic=epic,
            deal_id=None,
            side="BLOCKED",
            size=0,
            price=0,
            sl=sl_price,
            tp=tp_price,
            timestamp=timestamp(),
            timeframe=timeframe,
        )

        return jsonify({"status": "blocked", "reason": "price_unavailable"}), 200

    entry_price = (bid + offer) / 2
    print(f"[WEBHOOK] Entry price midpoint: {entry_price}", flush=True)

    size_info = calculate_size(
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        direction=action,
    )

    if size_info["blocked"]:
        print(f"[WEBHOOK] SIZING BLOCKED: {size_info['reason']}", flush=True)

        log_trade(
            ticker=symbol,
            epic=epic,
            deal_id=None,
            side="BLOCKED",
            size=0,
            price=entry_price,
            sl=sl_price,
            tp=tp_price,
            timestamp=timestamp(),
            timeframe=timeframe,
        )

        return jsonify({"status": "blocked", "reason": size_info["reason"]}), 200

    size = size_info["size"]

    print("[WEBHOOK] Final SL:", sl_price, flush=True)
    print("[WEBHOOK] Final TP:", tp_price, flush=True)
    print("[WEBHOOK] Final SIZE:", size, flush=True)

    result = place_order(epic, action, size, sl_price, tp_price)

    session.update_last_trade()

    try:
        deal_id = None

        if isinstance(result, dict):
            deal_id = (
                result.get("dealId")
                or result.get("dealReference")
                or result.get("deal_id")
            )

        ts = timestamp()

        log_trade(
            ticker=symbol,
            epic=epic,
            deal_id=deal_id,
            side=action,
            size=size,
            price=entry_price,
            sl=sl_price,
            tp=tp_price,
            timestamp=ts,
            timeframe=timeframe,
        )

        print(
            f"[WEBHOOK] OPEN TRADE LOGGED → {symbol} {action} size={size} dealId={deal_id}",
            flush=True,
        )

    except Exception as e:
        print(f"[WEBHOOK] log_trade failed: {e}", flush=True)

    return jsonify({"status": "ok", "result": result}), 200


app.register_blueprint(dashboard_blueprint)


@app.route("/raw")
def raw_positions():
    return jsonify(session.fetch_positions_from(API_POSITIONS))


@app.route("/raw/account")
def raw_account():
    r = session.request("GET", API_ACCOUNTS)
    return jsonify(r.json() if r else {})


@app.route("/")
def root():
    return redirect("/dashboard")


@app.route("/close/<position_id>", methods=["POST"])
def close_position(position_id):
    result = close_position_module(position_id)
    return jsonify(result), 200


if __name__ == "__main__":
    try:
        auth.ensure_token()
        print("[Webhook] Auth token ensured.", flush=True)
    except Exception as e:
        print(f"[Webhook] Auth ensure_token failed: {e}", flush=True)

    print("[Webhook] Starting scheduler...", flush=True)
    start_scheduler()
    print("[Webhook] Scheduler started.", flush=True)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
