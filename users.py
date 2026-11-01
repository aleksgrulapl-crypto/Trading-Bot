"""User accounts for the dashboard.

Accounts are stored as JSON on the persistent volume (same pattern as the
other stores). The Owner is not stored here - the Owner logs in with the
DASHBOARD_OWNER_PASSWORD environment variable.

Account roles:
    pending  - registered, waiting for Owner approval (sees only a waiting page)
    viewer   - approved by the Owner
    investor - upgraded by the Owner
"""
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

try:
    import config
except Exception:  # pragma: no cover
    config = None

logger = logging.getLogger("users")

USERS_PATH = (
    os.environ.get("USERS_PATH")
    or (getattr(config, "USERS_PATH", None) if config else None)
    or "/data/users.json"
)

ROLES = ("pending", "viewer", "investor")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.RLock()

# In-memory brute-force throttle: key -> list of recent failure timestamps.
_FAIL_WINDOW_SECONDS = 15 * 60
_FAIL_LIMIT = 10
_failures = {}

# Used so that unknown emails cost the same time as known ones.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def normalize_email(email) -> str:
    return str(email or "").strip().lower()


def _load():
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("failed to read users file %s", USERS_PATH)
        raise


def _save(users):
    directory = os.path.dirname(USERS_PATH) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(users, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, USERS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _public(user):
    return {k: v for k, v in user.items() if k != "password_hash"}


def validate_registration(email, password, name):
    """Return an error code, or None when the input is acceptable."""
    if not name or len(name.strip()) < 2 or len(name) > 80:
        return "invalid_name"
    if not _EMAIL_RE.match(email) or len(email) > 254:
        return "invalid_email"
    if len(password) < MIN_PASSWORD_LENGTH:
        return "password_too_short"
    if len(password) > MAX_PASSWORD_LENGTH:
        return "password_too_long"
    return None


def register(email, password, name):
    """Create a pending account. Returns (ok, user_or_None, status)."""
    email = normalize_email(email)
    name = " ".join(str(name or "").split())
    password = str(password or "")
    err = validate_registration(email, password, name)
    if err:
        return False, None, err
    with _lock:
        users = _load()
        if any(u.get("email") == email for u in users):
            return False, None, "email_taken"
        user = {
            "id": uuid.uuid4().hex,
            "email": email,
            "name": name,
            "password_hash": generate_password_hash(password),
            "role": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "risk_acknowledged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        users.append(user)
        _save(users)
    return True, _public(user), "ok"


def is_throttled(key) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < _FAIL_WINDOW_SECONDS]
    _failures[key] = recent
    return len(recent) >= _FAIL_LIMIT


def record_failure(key):
    _failures.setdefault(key, []).append(time.time())


def clear_failures(key):
    _failures.pop(key, None)


def authenticate(email, password):
    """Return the public user dict when the credentials are valid, else None."""
    email = normalize_email(email)
    password = str(password or "")
    with _lock:
        user = next((u for u in _load() if u.get("email") == email), None)
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if not check_password_hash(user.get("password_hash", ""), password):
        return None
    return _public(user)


def get_user(user_id):
    with _lock:
        user = next((u for u in _load() if u.get("id") == user_id), None)
    return _public(user) if user else None


def get_user_by_email(email):
    email = normalize_email(email)
    with _lock:
        user = next((u for u in _load() if u.get("email") == email), None)
    return _public(user) if user else None


def password_fingerprint(user_id):
    """Short value that changes whenever the password changes; used to make
    password-reset links single-use."""
    with _lock:
        user = next((u for u in _load() if u.get("id") == user_id), None)
    return (user.get("password_hash") or "")[-16:] if user else None


def set_password(user_id, new_password):
    """Returns (ok, status)."""
    new_password = str(new_password or "")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return False, "password_too_short"
    if len(new_password) > MAX_PASSWORD_LENGTH:
        return False, "password_too_long"
    with _lock:
        users = _load()
        for u in users:
            if u.get("id") == user_id:
                u["password_hash"] = generate_password_hash(new_password)
                _save(users)
                return True, "ok"
    return False, "not_found"


def list_users():
    with _lock:
        users = [_public(u) for u in _load()]
    order = {"pending": 0, "viewer": 1, "investor": 2}
    return sorted(users, key=lambda u: (order.get(u.get("role"), 9), u.get("created_at") or ""))


def set_role(user_id, role):
    """Returns (ok, status)."""
    if role not in ROLES:
        return False, "invalid_role"
    with _lock:
        users = _load()
        for u in users:
            if u.get("id") == user_id:
                u["role"] = role
                _save(users)
                return True, "ok"
    return False, "not_found"


def delete_user(user_id):
    with _lock:
        users = _load()
        remaining = [u for u in users if u.get("id") != user_id]
        if len(remaining) == len(users):
            return False, "not_found"
        _save(remaining)
    return True, "ok"
