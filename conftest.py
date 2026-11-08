"""Test-wide fixtures.

The dashboard now identifies users from a signed Flask session. The older
tests were written around plain `dashboard_auth` / `dashboard_role` cookies, so
they run against a small shim that reads those cookies instead. The real
session-based flow is covered in test_user_accounts.py, which opts out.
"""
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
