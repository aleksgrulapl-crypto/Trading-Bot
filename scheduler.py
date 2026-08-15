# ============================
# SCHEDULER MODULE (RESTORED + MODERNISED + SAFE)
# ============================

import threading
import time
from datetime import datetime
import pytz
import json

import session
import report
from utils import timestamp

UK_TZ = pytz.timezone("Europe/London")

# ---------------------------------------------------------
# SAFE FILE APPEND
# ---------------------------------------------------------

def append_json_line(filename, entry):
    try:
        with open(filename, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[Scheduler] File write error ({filename}): {e}")

# ---------------------------------------------------------
# LOG AVAILABLE BALANCE (Hourly)
# ---------------------------------------------------------

def log_available():
    try:
        raw = session.get_account()
        account = session.enrich_account(raw)

        entry = {
            "timestamp": timestamp(),
            "available": account.get("available", 0)
        }

        append_json_line("available_log.json", entry)
        print(f"[Scheduler] Logged available balance: £{entry['available']}")

    except Exception as e:
        print(f"[Scheduler] Error logging available balance: {e}")

# ---------------------------------------------------------
# LOG EQUITY (Daily)
# ---------------------------------------------------------

def log_equity():
    try:
        raw = session.get_account()
        account = session.enrich_account(raw)

        entry = {
            "timestamp": timestamp(),
            "equity": account.get("equity", 0)
        }

        append_json_line("equity_log.json", entry)
        print(f"[Scheduler] Logged daily equity: £{entry['equity']}")

    except Exception as e:
        print(f"[Scheduler] Error logging equity: {e}")

# ---------------------------------------------------------
# DAILY REPORT (RESTORED)
# ---------------------------------------------------------

def run_daily_report():
    try:
        report.generate_daily_report()
        session.shared_state["daily_report"] = session.get_daily_report()
        print("[Scheduler] Daily report generated.")
    except Exception as e:
        print(f"[Scheduler] Error generating daily report: {e}")

# ---------------------------------------------------------
# SCHEDULER CLASS
# ---------------------------------------------------------

class Scheduler:
    def __init__(self):
        self.jobs = []
        self.running = False

    def add_daily_job(self, hour, minute, func):
        self.jobs.append({
            "hour": hour,
            "minute": minute,
            "func": func,
            "hourly": False,
            "last_run": None
        })

    def add_hourly_job(self, minute, func):
        self.jobs.append({
            "hour": None,
            "minute": minute,
            "func": func,
            "hourly": True,
            "last_run": None
        })

    def start(self):
        if self.running:
            return

        self.running = True
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        while self.running:
            now_uk = datetime.now(UK_TZ)

            for job in self.jobs:
                # DAILY JOBS
                if not job["hourly"]:
                    if now_uk.hour == job["hour"] and now_uk.minute == job["minute"]:
                        # Prevent duplicate runs
                        if job["last_run"] != now_uk.date():
                            print(f"[Scheduler] Running daily job: {job['func'].__name__}")
                            try:
                                job["func"]()
                                job["last_run"] = now_uk.date()
                            except Exception as e:
                                print(f"[Scheduler] Error running daily job {job['func'].__name__}: {e}")

                # HOURLY JOBS
                else:
                    if now_uk.minute == job["minute"]:
                        # Prevent duplicate runs
                        hour_key = (now_uk.year, now_uk.month, now_uk.day, now_uk.hour)
                        if job["last_run"] != hour_key:
                            print(f"[Scheduler] Running hourly job: {job['func'].__name__}")
                            try:
                                job["func"]()
                                job["last_run"] = hour_key
                            except Exception as e:
                                print(f"[Scheduler] Error running hourly job {job['func'].__name__}: {e}")

            # Update scheduler heartbeat
            session.shared_state["system_status"]["last_scheduler"] = timestamp()

            time.sleep(30)

# ---------------------------------------------------------
# GLOBAL SCHEDULER INSTANCE
# ---------------------------------------------------------

scheduler = Scheduler()

# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

def start_scheduler():
    """
    Called from webhook.py when the Flask app starts.
    """

    # Daily report at 21:00 UK time
    scheduler.add_daily_job(21, 0, run_daily_report)

    # Log equity at 21:00 UK time
    scheduler.add_daily_job(21, 0, log_equity)

    # Log available balance every hour at HH:00
    scheduler.add_hourly_job(0, log_available)

    scheduler.start()
    print("[Scheduler] Started background scheduler.")
