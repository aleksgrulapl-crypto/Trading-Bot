# ============================
# SCHEDULER MODULE (RESTORED — TrailSL ONLY)
# ============================

import threading
import time
from datetime import datetime
import pytz
import json

import session
from utils import timestamp

from trail_sl import run_trailing_sl

UK_TZ = pytz.timezone("Europe/London")


def append_json_line(filename, entry):
    try:
        with open(filename, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[Scheduler] File write error ({filename}): {e}")


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
            "interval": None,
            "last_run": None
        })

    def add_hourly_job(self, minute, func):
        self.jobs.append({
            "hour": None,
            "minute": minute,
            "func": func,
            "hourly": True,
            "interval": None,
            "last_run": None
        })

    def add_interval_job(self, interval_seconds, func):
        self.jobs.append({
            "hour": None,
            "minute": None,
            "func": func,
            "hourly": False,
            "interval": interval_seconds,
            "last_run": 0
        })

    def start(self):
        if self.running:
            return

        self.running = True
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        while self.running:
            now = time.time()
            now_uk = datetime.now(UK_TZ)

            for job in self.jobs:

                if job["interval"]:
                    if now - job["last_run"] >= job["interval"]:
                        print(f"[Scheduler] Running interval job: {job['func'].__name__}")
                        try:
                            job["func"]()
                            job["last_run"] = now
                        except Exception as e:
                            print(f"[Scheduler] Error running interval job {job['func'].__name__}: {e}")
                    continue

                if not job["hourly"]:
                    if now_uk.hour == job["hour"] and now_uk.minute == job["minute"]:
                        if job["last_run"] != now_uk.date():
                            print(f"[Scheduler] Running daily job: {job['func'].__name__}")
                            try:
                                job["func"]()
                                job["last_run"] = now_uk.date()
                            except Exception as e:
                                print(f"[Scheduler] Error running daily job {job['func'].__name__}: {e}")

                else:
                    if now_uk.minute == job["minute"]:
                        hour_key = (now_uk.year, now_uk.month, now_uk.day, now_uk.hour)
                        if job["last_run"] != hour_key:
                            print(f"[Scheduler] Running hourly job: {job['func'].__name__}")
                            try:
                                job["func"]()
                                job["last_run"] = hour_key
                            except Exception as e:
                                print(f"[Scheduler] Error running hourly job {job['func'].__name__}: {e}")

            session.shared_state["system_status"]["last_scheduler"] = timestamp()
            time.sleep(30)


scheduler = Scheduler()


def start_scheduler():
    # Trail SL ONLY — no history import, no daily report
    scheduler.add_interval_job(60, run_trailing_sl)

    scheduler.start()
    print("[Scheduler] Started background scheduler (TrailSL only).")
