# gallery.py
# Owner-uploaded gallery of best trades. Images live under config.GALLERY_DIR
# with server-generated names; captions are kept in an index.json beside them.
# Only raster formats identified by their file signature are accepted.

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("gallery")

_lock = threading.Lock()
MAX_BYTES = 5 * 1024 * 1024
MAX_CAPTION = 200
_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")


def _dir() -> str:
    return getattr(config, "GALLERY_DIR", "/data/gallery")


def _index_path() -> str:
    return os.path.join(_dir(), "index.json")


def detect_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _load() -> List[Dict[str, Any]]:
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("gallery: failed to read index")
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    os.makedirs(_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp, _index_path())
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def list_items() -> List[Dict[str, Any]]:
    with _lock:
        return sorted(_load(), key=lambda i: i.get("added", 0), reverse=True)


def add_image(data: bytes, caption: str) -> Tuple[bool, str]:
    if not data:
        return False, "no_file"
    if len(data) > MAX_BYTES:
        return False, "too_large"
    ext = detect_type(data)
    if not ext:
        return False, "bad_type"
    name = f"{uuid.uuid4().hex}.{ext}"
    with _lock:
        try:
            os.makedirs(_dir(), exist_ok=True)
            with open(os.path.join(_dir(), name), "wb") as f:
                f.write(data)
            items = _load()
            items.append({"file": name, "caption": (caption or "").strip()[:MAX_CAPTION], "added": time.time()})
            _save(items)
        except Exception:
            logger.exception("gallery: failed to store image")
            return False, "save_failed"
    return True, name


def delete_image(name: str) -> bool:
    if not _NAME_RE.match(name or ""):
        return False
    with _lock:
        items = _load()
        kept = [i for i in items if i.get("file") != name]
        if len(kept) == len(items):
            return False
        try:
            _save(kept)
            path = os.path.join(_dir(), name)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            logger.exception("gallery: failed to delete %s", name)
            return False
    return True


def image_path(name: str) -> Optional[str]:
    """Absolute path for a stored image name, or None for anything unexpected."""
    if not _NAME_RE.match(name or ""):
        return None
    path = os.path.join(_dir(), name)
    return path if os.path.isfile(path) else None
