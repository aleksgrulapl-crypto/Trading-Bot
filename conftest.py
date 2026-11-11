"""Test-wide fixtures.

The dashboard now identifies users from a signed Flask session. The older
tests were written around plain `dashboard_auth` / `dashboard_role` cookies, so
they run against a small shim that reads those cookies instead. The real
session-based flow is covered in test_user_accounts.py, which opts out.
"""
import os
import tempfile

import pytest

# Several modules read their /data paths from the environment at import time,
# so point them at a scratch dir before anything imports them. Otherwise CI
# runners (which have no /data) fail and local runs write into a real volume.
_SCRATCH = tempfile.mkdtemp(prefix="tradingbot-tests-")
for _name, _file in {
    "TRADE_LOG_PATH": "trade_log.json", "DEPOSITS_LOG_PATH": "deposits_log.json",
    "INVESTOR_TIERS_PATH": "investor_tiers.json", "INVESTOR_PLEDGES_PATH": "investor_pledges.json",
    "WITHDRAWAL_REQUESTS_PATH": "withdrawal_requests.json", "BALANCE_STATE_PATH": "balance_state.json",
    "BALANCE_HISTORY_PATH": "balance_history.json", "OVERNIGHT_FEE_STATE_PATH": "overnight_fee_state.json",
    "WEEKLY_CLOSES_PATH": "weekly_closes.json", "PUBLIC_SNAPSHOT_PATH": "public_snapshot.json",
    "USERS_PATH": "users.json", "NOTIFY_SETTINGS_PATH": "notify_settings.json",
    "TRAIL_SETTINGS_PATH": "trail_settings.json", "TRADING_SWITCH_PATH": "trading_switch.json",
    "UI_PREFS_PATH": "ui_prefs.json", "TERMS_VERSION_PATH": "terms_version.json",
    "GALLERY_DIR": "gallery",
    "EVENTS_CACHE_PATH": "events_cache.json",
}.items():
    os.environ[_name] = os.path.join(_SCRATCH, _file)
from flask import request


def _legacy_cookie_identity():
    if not request.cookies.get("dashboard_auth"):
        return {"role": "viewer", "name": None, "logged_in": False}
    role = request.cookies.get("dashboard_role")
    if role not in ("owner", "viewer", "investor"):
        role = "owner"
    return {"role": role, "name": None, "logged_in": True}


@pytest.fixture(autouse=True)
def _legacy_cookie_auth(request, monkeypatch):
    if request.module.__name__ in ("test_user_accounts", "test_settings"):
        return
    import dashboard

    monkeypatch.setattr(dashboard, "_read_identity", _legacy_cookie_identity)
