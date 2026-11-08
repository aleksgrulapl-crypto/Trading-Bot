# ui_prefs.py
# Persisted Owner display preferences: which per-position controls (SL Trail,
# Close, Hedge) are shown in Open Positions, and which stocks' sentiment
# changes have been acknowledged. Hiding a control never disables the feature.

import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict

import config

logger = logging.getLogger("ui_prefs")

_lock = threading.Lock()
CONTROLS = ("sltrail", "close", "hedge")


def _path() -> str:
    return getattr(config, "UI_PREFS_PATH", "/data/ui_prefs.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("ui_prefs: failed to read %s", _path())
        return {}


def _save(data: Dict[str, Any]) -> bool:
    path = _path()
    try:
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            return True
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception:
        logger.exception("ui_prefs: failed to persist %s", path)
        return False


def controls() -> Dict[str, bool]:
    """Visibility of each position control; all default to shown."""
    with _lock:
        stored = _load().get("controls") or {}
    return {c: bool(stored.get(c, True)) for c in CONTROLS}


def set_control(name: str, shown: bool) -> bool:
    if name not in CONTROLS:
        return False
    with _lock:
        data = _load()
        ctl = data.get("controls") if isinstance(data.get("controls"), dict) else {}
        ctl[name] = bool(shown)
        data["controls"] = ctl
        return _save(data)


def sentiment_seen() -> Dict[str, str]:
    with _lock:
        seen = _load().get("sentiment_seen")
    return dict(seen) if isinstance(seen, dict) else {}


def update_sentiment_seen(seen: Dict[str, str]) -> bool:
    with _lock:
        data = _load()
        if data.get("sentiment_seen") == seen:
            return True
        data["sentiment_seen"] = seen
        return _save(data)
