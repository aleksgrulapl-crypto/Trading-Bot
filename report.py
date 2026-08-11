# ============================
# DAILY REPORT MODULE (FINAL)
# ============================

import json
from datetime import datetime
import os
import session
from trade_log import load_log
import utils

DAILY_REPORT_FILE = "daily_report.json"

# ---------------------------------------------------------
# LOAD / SAVE
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
    with open(DAILY_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=4)

# ---------------------------------------------------------
# FILTER TODAY'S TRADES
# ---------------------------------------------------------

def filter_today_trades(trades):
    today = datetime.now().strftime("%Y-%m-%d")
    return [t for t in trades if t["time"].startswith(today)]

# ---------------------------------------------------------
# CLOSED PNL
# ---------------------------------------------------------

def calculate_closed_pnl(trades_today):
    closed = [t for t in trades_today if t["side"] == "CLOSE"]
    return sum(t.get("pnl", 0) for t in closed)

# ---------------------------------------------------------
# OPEN PNL (positions still open)
# ---------------------------------------------------------

def calculate_open_pnl():
    positions = session.get_positions()
    total = 0.0
    for p in positions:
        # Prefer your own calculated PnL if available
        if p.get("profitLoss") is not None:
            total += p["profitLoss"]
        else:
            total += p.get("profit", 0)
    return total

# ---------------------------------------------------------
# WIN RATE
# ---------------------------------------------------------

def calculate_win_rate(trades_today):
    closed = [t for t in trades_today if t["side"] == "CLOSE"]
    if not closed:
        return 0
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    return round((wins / len(closed)) * 100, 2)

# ---------------------------------------------------------
# BEST / WORST TICKER
# ---------------------------------------------------------

def best_and_worst_ticker(trades_today):
    closed = [t for t in trades_today if t["side"] == "CLOSE"]
    if not closed:
        return None, None

    pnl_by_ticker = {}
    for t in closed:
        pnl_by_ticker.setdefault(t["ticker"], 0)
        pnl_by_ticker[t["ticker"]] += t.get("pnl", 0)

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

    # Load all trades
    all_trades = load_log()

    # Filter today's trades
    trades_today = filter_today_trades(all_trades)

    # Metrics
    closed_pnl = calculate_closed_pnl(trades_today)
    open_pnl = calculate_open_pnl()
    win_rate = calculate_win_rate(trades_today)
    best_ticker, worst_ticker = best_and_worst_ticker(trades_today)

    # Build report
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "closed_pnl": round(closed_pnl, 2),
        "open_pnl": round(open_pnl, 2),
        "total_trades": len(trades_today),
        "win_rate": win_rate,
        "best_ticker": best_ticker,
        "worst_ticker": worst_ticker,
        "trades": trades_today
    }

    # Save to file
    save_daily_report(report)

    # Update shared state
    session.shared_state["daily_report"] = report
    session.shared_state["trade_log"] = all_trades

    print(f"[Daily Report] Generated report for {report['date']}")
    return report
