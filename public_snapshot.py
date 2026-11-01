"""Public landing-page performance snapshot.

Only percentages for the last *completed* week and month are published (no
balances, amounts or positions). The figures are computed by a scheduled job
and written to a small JSON file, so serving the public page never touches the
broker or the trade log.
"""
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import config
import deposits

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = os.environ.get("PUBLIC_SNAPSHOT_PATH") or getattr(config, "PUBLIC_SNAPSHOT_PATH", "/data/public_snapshot.json")
_CACHE_SECONDS = 60
_lock = threading.Lock()
_cache: Dict[str, Any] = {"ts": 0.0, "path": None, "data": None}


def build_snapshot(pnl_events, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or deposits._now_naive_uk()
    weeks = deposits.compute_successful_weeks(now=now, pnl_events=pnl_events)
    total = weeks["total_weeks"]
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "last_week": deposits.compute_last_completed_week(now=now, pnl_events=pnl_events),
        "last_month": deposits.compute_last_completed_month(now=now, pnl_events=pnl_events),
        "hit_rate": {
            "successful_weeks": weeks["successful_weeks"],
            "total_weeks": total,
            "pct": round(weeks["successful_weeks"] / total * 100) if total else None,
            "target_pct": deposits.SUCCESSFUL_WEEK_PCT,
        },
        "avg_weekly_return": weeks["avg_weekly_return"],
    }


def write_snapshot(snapshot: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or SNAPSHOT_PATH
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    with _lock:
        _cache["ts"] = 0.0


def load_snapshot(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the cached snapshot, or None if it has not been generated yet."""
    path = path or SNAPSHOT_PATH
    now = time.time()
    with _lock:
        if _cache["path"] == path and now - _cache["ts"] < _CACHE_SECONDS:
            return _cache["data"]
    data = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("public_snapshot: could not read %s", path)
    with _lock:
        _cache.update(ts=now, path=path, data=data)
    return data
