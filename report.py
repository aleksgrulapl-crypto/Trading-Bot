import json
from datetime import datetime, timedelta
import os
import session
from trade_log import load_log
import utils

DAILY_REPORT_FILE = "daily_report.json"


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


def filter_today_trades(trades):
    today = datetime.now().strftime("%Y-%m-%d")
    return [t for t in trades if t["time"].startswith(today)]


def calculate_closed_pnl(trades_today):
    closed = [t for t in trades_today if t["side"] == "CLOSE"]
    return sum(t.get("pnl", 0) for t in closed)


def calculate_open_pnl():
    positions = session.get_positions()
    return sum(p["profit"] for p in positions)


def calculate_win_rate(trades_today):
    closed = [t for t in trades_today if t["side"] == "CLOSE"]
    if not closed:
        return 0

    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    return round((wins / len(closed)) * 100, 2)


def generate_daily_report():
    """
    Creates a detailed daily report (Option B) at 21:00 UK time.
    """

    # Load all trades
    all_trades = load_log()

    # Filter today's trades
    trades_today = filter_today_trades(all_trades)

    # Calculate metrics
    closed_pnl = calculate_closed_pnl(trades_today)
    open_pnl = calculate_open_pnl()
    win_rate = calculate_win_rate(trades_today)

    # Build detailed report
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "closed_pnl": round(closed_pnl, 2),
        "open_pnl": round(open_pnl, 2),
        "trades": trades_today,
        "win_rate": win_rate
    }

    # Save to file
    save_daily_report(report)

    # Store in shared state for dashboard
    session.shared_state["daily_report"] = report

    return report
