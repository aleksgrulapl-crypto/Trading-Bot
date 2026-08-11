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
# AVAILABLE BALANCE LOGGING
# ---------------------------------------------------------

def log_available():
    """
    Logs available balance once per hour for trend chart.
    """
    try:
        account = session.get_account()  # your existing function
        entry = {
            "timestamp": timestamp(),
            "available": account["available"]
        }

        with open("available_log.json", "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[Scheduler] Logged available balance: £{account['available']}")

    except Exception as e:
        print(f"[Scheduler] Error logging available balance: {e}")


# ---------------------------------------------------------
# SCHEDULER CLASS
# ---------------------------------------------------------

class Scheduler:
    def __init__(self):
        self.jobs = []
        self.running = False

    def add_daily_job(self, hour, minute, func):
        """
        Add a job that runs every day at a specific UK time.
        """
        self.jobs.append({
            "hour": hour,
            "minute": minute,
            "func": func
        })

    def add_hourly_job(self, minute, func):
        """
        Add a job that runs every hour at a specific minute.
        Example: minute=0 → runs at HH:00 every hour.
        """
        self.jobs.append({
            "hour": None,
            "minute": minute,
            "func": func,
            "hourly": True
        })

    def start(self):
        """
        Start the background scheduler thread.
        """
        if self.running:
            return

        self.running = True
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        """
        Main scheduler loop.
        Checks every 30 seconds.
        """
        while self.running:
            now_uk = datetime.now(UK_TZ)

            for job in self.jobs:

                # DAILY JOBS
                if "hourly" not in job:
                    if now_uk.hour == job["hour"] and now_uk.minute == job["minute"]:
                        print(f"[Scheduler] Running daily job: {job['func'].__name__}")
                        try:
                            job["func"]()
                        except Exception as e:
                            print(f"[Scheduler] Error running job {job['func'].__name__}: {e}")
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


def start_scheduler():
    """
    Called from webhook.py when the Flask app starts.
    """

    # Daily report at 21:00 UK time
    scheduler.add_daily_job(21, 0, report.generate_daily_report)

    # Log available balance every hour at HH:00
    scheduler.add_hourly_job(0, log_available)

    scheduler.start()
    print("[Scheduler] Started background scheduler.")
