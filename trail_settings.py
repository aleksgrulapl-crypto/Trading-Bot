# trail_settings.py
# Per-position SL-trail opt-out (dealIds with trailing switched Off), persisted
# to disk so it survives restarts and is shared by the dashboard and trail_sl.

import json
import logging
import os
import tempfile
import threading
from typing import Iterable, Set

import config

logger = logging.getLogger("trail_settings")

_lock = threading.Lock()


def _path() -> str:
    return getattr(config, "TRAIL_DISABLED_PATH", "/data/trail_disabled.json")


def _load() -> Set[str]:
    path = _path()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(x) for x in data} if isinstance(data, list) else set()
    except Exception:
        logger.exception("trail_settings: failed to read %s", path)
        return set()


def _save(ids: Set[str]) -> bool:
    path = _path()
    try:
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(sorted(ids), f)
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("trail_settings: failed to persist %s", path)
        return False


def disabled_ids() -> Set[str]:
    with _lock:
        return _load()


def is_trail_disabled(deal_id) -> bool:
    return str(deal_id) in disabled_ids()


def set_trail_enabled(deal_id, enabled: bool) -> bool:
    deal_id = str(deal_id)
    with _lock:
        ids = _load()
        if enabled:
            ids.discard(deal_id)
        else:
            ids.add(deal_id)
        return _save(ids)


def prune(live_ids: Iterable) -> None:
    """Forget closed positions so a reused dealId doesn't inherit an old opt-out."""
    live = {str(x) for x in live_ids}
    with _lock:
        ids = _load()
        kept = ids & live
        if kept != ids:
            _save(kept)
