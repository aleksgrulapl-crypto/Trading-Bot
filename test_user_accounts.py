"""Tests for registered user accounts and the signed-session login flow."""
import pytest
from flask import Flask

import dashboard as dashboard_module
import users


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_PATH", str(tmp_path / "users.json"))
    users._failures.clear()
    monkeypatch.setenv("DASHBOARD_OWNER_PASSWORD", "owner-pw-123")
    monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
        "account": {"equity": 1, "balance": 1, "pnl": 0, "available": 1},
        "positions": [], "combined_trades": [], "analytics": dashboard_module._safe_analytics({}),
        "weekly_analytics": dashboard_module._safe_analytics({}),
        "monthly_analytics": dashboard_module._safe_analytics({}),
    })

    def make_client():
        app = Flask(__name__, template_folder="templates")
        app.secret_key = "test-secret"
        app.register_blueprint(dashboard_module.dashboard)
        return app.test_client()

    return make_client


def _register(client, email="Jane@Example.com", name="Jane Doe", password="correct-horse", **overrides):
    data = {
        "name": name, "email": email, "password": password,
        "confirm_password": password, "accept_risk": "1",
    }
    data.update(overrides)
    return client.post("/dashboard/register", data=data)


def _approve(email, role="viewer"):
    user = next(u for u in users.list_users() if u["email"] == email)
    assert users.set_role(user["id"], role) == (True, "ok")
    return user


def test_anonymous_is_redirected_to_login(env):
    client = env()
    resp = client.get("/dashboard")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard/login")
    assert client.get("/dashboard/data").status_code == 401


def test_legal_pages_are_public(env):
    client = env()
    for path in ("/dashboard/terms", "/dashboard/faq", "/dashboard/contact", "/dashboard/login", "/dashboard/register"):
        assert client.get(path).status_code == 200, path


def test_forged_legacy_cookies_grant_nothing(env):
    client = env()
    client.set_cookie("dashboard_auth", "1")
    client.set_cookie("dashboard_role", "owner")
    assert client.get("/dashboard").status_code == 302
    assert client.post("/dashboard/users/x/role", data={"role": "investor"}).status_code in (401, 403)


def test_owner_login_with_env_password(env):
    client = env()
    bad = client.post("/dashboard/login", data={"username": "owner", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/dashboard/login", data={"username": "owner", "password": "owner-pw-123"})
    assert ok.status_code == 302
    assert client.get("/dashboard/users").status_code == 200


def test_owner_login_disabled_without_password(env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_OWNER_PASSWORD", raising=False)
    monkeypatch.setattr(dashboard_module.config, "DASHBOARD_OWNER_PASSWORD", "")
    client = env()
    assert client.post("/dashboard/login", data={"username": "owner", "password": ""}).status_code == 401


def test_register_creates_pending_account_with_hashed_password(env):
    client = env()
    resp = _register(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard/pending")
    stored = users._load()
    assert len(stored) == 1
    assert stored[0]["email"] == "jane@example.com"
    assert stored[0]["role"] == "pending"
    assert "correct-horse" not in stored[0]["password_hash"]


def test_pending_user_only_sees_waiting_page(env):
    client = env()
    _register(client)
    assert "Awaiting approval" in client.get("/dashboard/pending").get_data(as_text=True)
    assert client.get("/dashboard").headers["Location"].endswith("/dashboard/pending")
    assert client.get("/dashboard/trades").headers["Location"].endswith("/dashboard/pending")
    assert client.get("/dashboard/data").status_code == 403
    assert client.get("/dashboard/terms").status_code == 200


def test_approval_takes_effect_immediately_and_grants_viewer(env):
    client = env()
    _register(client)
    _approve("jane@example.com")
    assert client.get("/dashboard").status_code == 200
    assert client.get("/dashboard/pending").headers["Location"].endswith("/dashboard")
    # viewer restrictions still apply
    assert client.get("/dashboard/trades").headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard/users").headers["Location"].endswith("/dashboard")


def test_investor_upgrade_unlocks_roi_and_removal_locks_out(env):
    client = env()
    _register(client)
    user = _approve("jane@example.com")
    assert client.get("/dashboard/roi").status_code in (302,)
    users.set_role(user["id"], "investor")
    assert client.get("/dashboard/roi").status_code != 302
    users.delete_user(user["id"])
    assert client.get("/dashboard").headers["Location"].endswith("/dashboard/login")


def test_registered_user_can_log_in_with_email(env):
    _register(env())
    _approve("jane@example.com")
    client = env()
    assert client.post("/dashboard/login", data={"username": "JANE@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/dashboard/login", data={"username": "jane@example.com", "password": "correct-horse"}).status_code == 302
    assert client.get("/dashboard").status_code == 200


def test_registration_validation(env):
    client = env()
    assert _register(client, password="short").status_code == 400
    assert _register(client, email="not-an-email").status_code == 400
    assert _register(client, confirm_password="different").status_code == 400
    assert _register(client, accept_risk="").status_code == 400
    assert _register(client, email="owner").status_code == 400
    assert users._load() == []


def test_duplicate_email_rejected(env):
    _register(env())
    resp = _register(env(), email="JANE@example.com")
    assert resp.status_code == 400
    assert len(users._load()) == 1


def test_login_throttled_after_repeated_failures(env):
    client = env()
    for _ in range(users._FAIL_LIMIT):
        assert client.post("/dashboard/login", data={"username": "owner", "password": "x"}).status_code == 401
    resp = client.post("/dashboard/login", data={"username": "owner", "password": "owner-pw-123"})
    assert resp.status_code == 429


def test_only_owner_can_change_roles(env):
    client = env()
    _register(client)
    user = users.list_users()[0]
    assert client.post(f"/dashboard/users/{user['id']}/role", data={"role": "investor"}).status_code == 403
    assert users.get_user(user["id"])["role"] == "pending"

    owner = env()
    owner.post("/dashboard/login", data={"username": "owner", "password": "owner-pw-123"})
    assert owner.post(f"/dashboard/users/{user['id']}/role", data={"role": "bogus"}, headers={"Accept": "application/json"}).status_code in (302, 400)
    assert users.get_user(user["id"])["role"] == "pending"
    assert owner.post(f"/dashboard/users/{user['id']}/role", data={"role": "viewer"}).status_code == 302
    assert users.get_user(user["id"])["role"] == "viewer"
    assert owner.post(f"/dashboard/users/{user['id']}/delete").status_code == 302
    assert users.list_users() == []


def test_logout_clears_session(env):
    client = env()
    _register(client)
    _approve("jane@example.com")
    assert client.get("/dashboard").status_code == 200
    client.get("/dashboard/logout")
    assert client.get("/dashboard").headers["Location"].endswith("/dashboard/login")
