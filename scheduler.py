# scheduler.py
# ============================
# SCHEDULER MODULE (TrailSL + ClosedSync)
# ============================

import threading
import time
import json
import logging
from datetime import datetime
from typing import Callable, Any

import pytz

import session
import config
from utils import timestamp

from trail_sl import run_trailing_sl
from history_sync import sync_closed_trades  # only import the function

UK_TZ = pytz.timezone("Europe/London")

logger = logging.getLogger("scheduler")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [scheduler] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)


def append_json_line(filename: str, entry: Any) -> None:
    """
    Append a JSON line to filename. Fail silently but log errors.
    """
    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.exception("File write error (%s): %s", filename, e)


def log_available(logfile: str = "available_log.json") -> None:
    """
    Log available balance to a JSON-lines file.
    """
    try:
        raw = session.get_account()
        account = session.enrich_account(raw) or {}

        entry = {
            "timestamp": timestamp(),
            "available": account.get("available", 0)
        }

        append_json_line(logfile, entry)
        logger.info("Logged available balance: %s", entry["available"])
    except Exception as e:
        logger.exception("Error logging available balance: %s", e)


def log_equity(logfile: str = "equity_log.json") -> None:
    """
    Log equity to a JSON-lines file.
    """
    try:
        raw = session.get_account()
        account = session.enrich_account(raw) or {}

        entry = {
            "timestamp": timestamp(),
            "equity": account.get("equity", 0)
        }

        append_json_line(logfile, entry)
        logger.info("Logged equity: %s", entry["equity"])
    except Exception as e:
        logger.exception("Error logging equity: %s", e)


class Scheduler:
    """
    Simple scheduler supporting:
      - daily jobs at hour/minute (UK timezone)
      - hourly jobs at minute
      - interval jobs (seconds)
    Thread-safe enough for this use-case (single background thread).
    """

    def __init__(self):
        self.jobs = []
        self.running = False
        self._lock = threading.Lock()

    def add_daily_job(self, hour: int, minute: int, func: Callable[[], None]) -> None:
        with self._lock:
            self.jobs.append({
                "type": "daily",
                "hour": int(hour),
                "minute": int(minute),
                "func": func,
                "last_run_date": None
            })

    def add_hourly_job(self, minute: int, func: Callable[[], None]) -> None:
        with self._lock:
            self.jobs.append({
                "type": "hourly",
                "minute": int(minute),
                "func": func,
                "last_run_hour_key": None
            })

    def add_interval_job(self, interval_seconds: int, func: Callable[[], None]) -> None:
        with self._lock:
            self.jobs.append({
                "type": "interval",
                "interval": int(interval_seconds),
                "func": func,
                "last_run": 0.0
            })

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self.run, daemon=True, name="SchedulerThread")
        thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self.running = False
        logger.info("Scheduler stopping")

    def run(self) -> None:
        """
        Main loop: checks jobs and runs them when due.
        Sleeps a short interval between checks to be responsive.
        """
        sleep_interval = 10  # seconds between loop iterations
        while self.running:
            now_ts = time.time()
            now_uk = datetime.now(UK_TZ)

            with self._lock:
                for job in list(self.jobs):
                    try:
                        if job["type"] == "interval":
                            last = job.get("last_run", 0.0)
                            if now_ts - last >= job["interval"]:
                                logger.debug("Running interval job: %s", job["func"].__name__)
                                try:
                                    job["func"]()
                                except Exception:
                                    logger.exception("Error running interval job %s", job["func"].__name__)
                                job["last_run"] = now_ts
                        elif job["type"] == "daily":
                            # run once per day at specified hour/minute (UK timezone)
                            if now_uk.hour == job["hour"] and now_uk.minute == job["minute"]:
                                today = now_uk.date()
                                if job.get("last_run_date") != today:
                                    logger.debug("Running daily job: %s", job["func"].__name__)
                                    try:
                                        job["func"]()
                                    except Exception:
                                        logger.exception("Error running daily job %s", job["func"].__name__)
                                    job["last_run_date"] = today
                        elif job["type"] == "hourly":
                            # run once per hour at specified minute
                            if now_uk.minute == job["minute"]:
                                hour_key = (now_uk.year, now_uk.month, now_uk.day, now_uk.hour)
                                if job.get("last_run_hour_key") != hour_key:
                                    logger.debug("Running hourly job: %s", job["func"].__name__)
                                    try:
                                        job["func"]()
                                    except Exception:
                                        logger.exception("Error running hourly job %s", job["func"].__name__)
                                    job["last_run_hour_key"] = hour_key
                    except Exception:
                        logger.exception("Unexpected error evaluating job: %s", job.get("func").__name__)

            # update shared state for monitoring
            try:
                session.shared_state.setdefault("system_status", {})["last_scheduler"] = timestamp()
            except Exception:
                logger.debug("Failed to update shared_state last_scheduler")

            time.sleep(sleep_interval)


# single scheduler instance
scheduler = Scheduler()


def start_scheduler() -> None:
    """
    Configure and start the scheduler with the desired jobs.
    - Trailing SL job runs every 5 seconds (short interval for responsiveness).
    - Closed trade sync job runs every 5 seconds.
    """
    # avoid adding duplicate jobs if start_scheduler called multiple times
    if scheduler.running:
        logger.info("Scheduler already running")
        return

    # Trail SL job
    scheduler.add_interval_job(5, run_trailing_sl)

    # Closed trade sync job (SL/TP/manual closes)
    scheduler.add_interval_job(5, sync_closed_trades)

    # Optional daily/hourly logging jobs (examples)
    scheduler.add_daily_job(getattr(config, "DAILY_REPORT_HOUR", 22), getattr(config, "DAILY_REPORT_MINUTE", 0), lambda: log_equity("equity_log.json"))
    scheduler.add_hourly_job(0, lambda: log_available("available_log.json"))

    scheduler.start()
    logger.info("Started background scheduler (TrailSL + ClosedSync).")
