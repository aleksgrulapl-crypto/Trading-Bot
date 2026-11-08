"""Test-wide fixtures.

The dashboard now identifies users from a signed Flask session. The older
tests were written around plain `dashboard_auth` / `dashboard_role` cookies, so
they run against a small shim that reads those cookies instead. The real
session-based flow is covered in test_user_accounts.py, which opts out.
"""
import os
import pytest
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


@pytest.fixture(autouse=True)
def _isolate_data_paths(tmp_path, monkeypatch):
    """Redirect every /data persistence path to a temp dir so tests never
    depend on (or write to) a real /data volume, e.g. on CI runners."""
    import config

    for name in dir(config):
        value = getattr(config, name)
        if name.endswith(("_PATH", "_FILE")) and isinstance(value, str) and value.replace("\\", "/").startswith("/data/"):
            monkeypatch.setattr(config, name, str(tmp_path / os.path.basename(value)))
