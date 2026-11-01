# trading_switch.py
# Owner-controlled master switch. When OFF the webhook rejects every new trade
# (the alert is logged to the Owner-only Rejected list); everything else keeps
# running. Persisted to disk so it survives restarts. Defaults to ON.

import json
import logging
import os
import tempfile
import threading

import config

logger = logging.getLogger("trading_switch")

_lock = threading.Lock()


def _path() -> str:
    return getattr(config, "TRADING_SWITCH_PATH", "/data/trading_switch.json")


def is_enabled() -> bool:
    path = _path()
    with _lock:
        if not os.path.exists(path):
            return True
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("enabled", True)) if isinstance(data, dict) else True
        except Exception:
            logger.exception("trading_switch: failed to read %s; defaulting to ON", path)
            return True


def set_enabled(enabled: bool) -> bool:
    path = _path()
    with _lock:
        try:
            folder = os.path.dirname(path) or "."
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"enabled": bool(enabled)}, f)
                os.replace(tmp, path)
                return True
            except Exception:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                raise
        except Exception:
            logger.exception("trading_switch: failed to persist %s", path)
            return False
