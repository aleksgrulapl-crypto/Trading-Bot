# ============================
# DAILY REPORT MODULE (RESTORED + MODERNISED)
# ============================

import json
from datetime import datetime
import os

import session
from trade_log import load_log
from utils import timestamp

DAILY_REPORT_FILE = "daily_report.json"

# ---------------------------------------------------------
# LOAD / SAVE (atomic)
# ---------------------------------------------------------

def load_daily_report():
    if not os.path.exists(DAILY_REPORT_FILE):
        return {}
    try:
        with open(DAILY_REPORT_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_daily_report(report):
    tmp = DAILY_REPORT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report, f, indent=4)
    os.replace(tmp, DAILY_REPORT_FILE)

# ---------------------------------------------------------
# FILTER TODAY'S TRADES
# ---------------------------------------------------------

def filter_today_trades(trades):
    today = datetime.now().strftime("%Y-%m-%d")
    safe = []

    for t in trades:
        try:
            if t.get("time", "").startswith(today):
                safe.append(t)
        except:
            continue

    return safe

# ---------------------------------------------------------
# CLOSED PNL
# ---------------------------------------------------------

def calculate_closed_pnl(trades_today):
    closed = [t for t in trades_today if t.get("side") == "CLOSE"]
    return sum(float(t.get("pnl", 0)) for t in closed)

# ---------------------------------------------------------
# OPEN PNL (positions still open)
# ---------------------------------------------------------

def calculate_open_pnl():
    raw_positions = session.get_positions()
    enriched = session.enrich_positions(raw_positions)
    return sum(float(p.get("profit", 0)) for p in enriched)

# ---------------------------------------------------------
# WIN RATE
# ---------------------------------------------------------

def calculate_win_rate(trades_today):
    closed = [t for t in trades_today if t.get("side") == "CLOSE"]
    if not closed:
        return 0
    wins = sum(1 for t in closed if float(t.get("pnl", 0)) > 0)
    return round((wins / len(closed)) * 100, 2)

# ---------------------------------------------------------
# BEST / WORST TICKER
# ---------------------------------------------------------

def best_and_worst_ticker(trades_today):
    closed = [t for t in trades_today if t.get("side") == "CLOSE"]
    if not closed:
        return None, None

    pnl_by_ticker = {}

    for t in closed:
        ticker = t.get("ticker")
        pnl = float(t.get("pnl", 0))

        if not ticker:
            continue

        pnl_by_ticker.setdefault(ticker, 0)
        pnl_by_ticker[ticker] += pnl

    if not pnl_by_ticker:
        return None, None

    best = max(pnl_by_ticker, key=pnl_by_ticker.get)
    worst = min(pnl_by_ticker, key=pnl_by_ticker.get)

    return best, worst

# ---------------------------------------------------------
# GENERATE DAILY REPORT
# ---------------------------------------------------------

def generate_daily_report():
    """
    Creates a detailed daily report at 21:00 UK time.
    """

    try:
        all_trades = load_log()
    except:
        all_trades = []

    trades_today = filter_today_trades(all_trades)

    closed_pnl = calculate_closed_pnl(trades_today)
    open_pnl = calculate_open_pnl()
    win_rate = calculate_win_rate(trades_today)
    best_ticker, worst_ticker = best_and_worst_ticker(trades_today)

    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": timestamp(),
        "closed_pnl": round(closed_pnl, 2),
        "open_pnl": round(open_pnl, 2),
        "total_trades": len(trades_today),
        "win_rate": win_rate,
        "best_ticker": best_ticker,
        "worst_ticker": worst_ticker,
        "trades": trades_today
    }

    save_daily_report(report)

    # Update shared state
    session.shared_state["daily_report"] = report
    session.shared_state["trade_log"] = all_trades
    session.shared_state["positions"] = session.enrich_positions(session.get_positions())
    session.shared_state["account"] = session.enrich_account(session.get_account())

    print(f"[Daily Report] Generated report for {report['date']}")
    return report
