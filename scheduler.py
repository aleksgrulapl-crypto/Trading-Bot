import threading
import time
from datetime import datetime
import pytz
import report
import session
import json
from utils import timestamp

UK_TZ = pytz.timezone("Europe/London")

# ---------------------------------------------------------
# LOG AVAILABLE BALANCE (Hourly)
# ---------------------------------------------------------

def log_available():
    try:
        account = session.get_account()
        entry = {
            "timestamp": timestamp(),
            "available": account.get("available")
        }

        with open("available_log.json", "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[Scheduler] Logged available balance: £{account.get('available')}")

    except Exception as e:
        print(f"[Scheduler] Error logging available balance: {e}")

# ---------------------------------------------------------
# LOG EQUITY (Daily)
# ---------------------------------------------------------

def log_equity():
    try:
        account = session.get_account()
        entry = {
            "timestamp": timestamp(),
            "equity": account.get("equity")
        }

        with open("equity_log.json", "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[Scheduler] Logged daily equity: £{account.get('equity')}")

    except Exception as e:
        print(f"[Scheduler] Error logging equity: {e}")

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
            "hourly": False
        })

    def add_hourly_job(self, minute, func):
        self.jobs.append({
            "hour": None,
            "minute": minute,
            "func": func,
            "hourly": True
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
                        print(f"[Scheduler] Running daily job: {job['func'].__name__}")
                        try:
                            job["func"]()
                        except Exception as e:
                            print(f"[Scheduler] Error running daily job {job['func'].__name__}: {e}")
                        time.sleep(60)

                # HOURLY JOBS
                else:
                    if now_uk.minute == job["minute"]:
                        print(f"[Scheduler] Running hourly job: {job['func'].__name__}")
                        try:
                            job["func"]()
                        except Exception as e:
                            print(f"[Scheduler] Error running hourly job {job['func'].__name__}: {e}")
                        time.sleep(60)

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
    scheduler.add_daily_job(21, 0, report.generate_daily_report)

    # Log equity at 21:00 UK time (same minute, but safe)
    scheduler.add_daily_job(21, 0, log_equity)

    # Log available balance every hour at HH:00
    scheduler.add_hourly_job(0, log_available)

    scheduler.start()
    print("[Scheduler] Started background scheduler.")
