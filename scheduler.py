import threading
import time
from datetime import datetime
import pytz
import report



UK_TZ = pytz.timezone("Europe/London")


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
                if now_uk.hour == job["hour"] and now_uk.minute == job["minute"]:
                    print(f"[Scheduler] Running daily job: {job['func'].__name__}")
                    try:
                        job["func"]()
                    except Exception as e:
                        print(f"[Scheduler] Error running job {job['func'].__name__}: {e}")

                    # Prevent double‑triggering within the same minute
                    time.sleep(60)

            time.sleep(30)


# Global scheduler instance
scheduler = Scheduler()


def start_scheduler():
    """
    Called from webhook.py when the Flask app starts.
    """
    # Daily report at 21:00 UK time
    scheduler.add_daily_job(21, 0, report.generate_daily_report)

    scheduler.start()
    print("[Scheduler] Started background scheduler.")
