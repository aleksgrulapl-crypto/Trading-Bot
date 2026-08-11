# ============================
# REPORT MODULE (RESTORED + VERIFIED)
# ============================

import json
import os
from config import DAILY_REPORT_FILE
from utils import timestamp

# ---------------------------------------------------------
# LOAD DAILY REPORT
# ---------------------------------------------------------

def get_daily_report():
    """
    Loads the daily report JSON file.
    Returns {} if file missing or unreadable.
    """

    try:
        if not os.path.exists(DAILY_REPORT_FILE):
            print("[REPORT] No daily report file found.")
            return {}

        with open(DAILY_REPORT_FILE, "r") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"[REPORT] Error reading daily report: {e}")
        return {}

# ---------------------------------------------------------
# SAVE DAILY REPORT
# ---------------------------------------------------------

def save_daily_report(report_data):
    """
    Saves the daily report JSON file.
    """

    try:
        with open(DAILY_REPORT_FILE, "w") as f:
            json.dump(report_data, f, indent=4)

        print("[REPORT] Daily report saved.")

    except Exception as e:
        print(f"[REPORT] Failed to save daily report: {e}")

# ---------------------------------------------------------
# GENERATE DAILY REPORT (OPTIONAL)
# ---------------------------------------------------------

def generate_daily_report(positions, account):
    """
    Generates a daily report structure.
    """

    report_data = {
        "timestamp": timestamp(),
        "positions": positions,
        "account": account
    }

    save_daily_report(report_data)
    return report_data
