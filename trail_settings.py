# trail_settings.py
# Per-position trailing state persisted to disk so it survives restarts and is
# shared by the dashboard and trail_sl:
#   enabled         Trader-only SL Trail opt-in (OFF unless switched on)
#   activation_gbp  optional Trader-chosen GBP profit at which the trail starts
#   tp_trail        TP was removed and the SL is now trailing the profit

import json
import logging
import os
import tempfile
import threading
from typing import Dict, Iterable, Optional

import config

logger = logging.getLogger("trail_settings")

_lock = threading.Lock()


def _path() -> str:
    return getattr(config, "TRAIL_SETTINGS_PATH", "/data/trail_settings.json")


def _load() -> Dict[str, dict]:
    path = _path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        logger.exception("trail_settings: failed to read %s", path)
        return {}


def _save(ids: Dict[str, dict]) -> bool:
    path = _path()
    try:
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(ids, f, sort_keys=True)
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


def all_settings() -> Dict[str, dict]:
    with _lock:
        return _load()


def get(deal_id) -> dict:
    rec = all_settings().get(str(deal_id), {})
    return {
        "enabled": bool(rec.get("enabled", False)),
        "activation_gbp": rec.get("activation_gbp"),
        "tp_trail": bool(rec.get("tp_trail", False)),
    }


def is_trail_enabled(deal_id) -> bool:
    return get(deal_id)["enabled"]


def set_trail(deal_id, enabled: Optional[bool] = None, activation_gbp=None, clear_activation: bool = False) -> bool:
    deal_id = str(deal_id)
    with _lock:
        data = _load()
        rec = data.setdefault(deal_id, {})
        if enabled is not None:
            rec["enabled"] = bool(enabled)
        if clear_activation:
            rec.pop("activation_gbp", None)
        elif activation_gbp is not None:
            rec["activation_gbp"] = float(activation_gbp)
        return _save(data)


def mark_tp_trail(deal_id) -> bool:
    deal_id = str(deal_id)
    with _lock:
        data = _load()
        data.setdefault(deal_id, {})["tp_trail"] = True
        return _save(data)


def prune(live_ids: Iterable) -> None:
    """Forget closed positions so a reused dealId doesn't inherit old state."""
    live = {str(x) for x in live_ids}
    with _lock:
        data = _load()
        kept = {k: v for k, v in data.items() if k in live}
        if kept != data:
            _save(kept)
