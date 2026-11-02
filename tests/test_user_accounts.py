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


@pytest.fixture
def sent(monkeypatch):
    import notifications
    box = []
    monkeypatch.setattr(notifications, "send_user_email", lambda to, subject, body: box.append((to, subject, body)) or True)
    monkeypatch.setattr(notifications, "notify_owner_new_registration", lambda n, e: box.append(("owner", n, e)))
    return box


def test_registration_sends_welcome_and_owner_notice(env, sent):
    _register(env())
    assert any(m[0] == "jane@example.com" and "has been noted" in m[2] for m in sent)
    assert ("owner", "Jane Doe", "jane@example.com") in sent


def test_approval_and_decline_emails(env, sent):
    _register(env())
    _register(env(), email="bob@example.com", name="Bob")
    sent.clear()
    owner = env()
    owner.post("/dashboard/login", data={"username": "owner", "password": "owner-pw-123"})
    jane = next(u for u in users.list_users() if u["email"] == "jane@example.com")
    bob = next(u for u in users.list_users() if u["email"] == "bob@example.com")
    owner.post(f"/dashboard/users/{jane['id']}/role", data={"role": "viewer"})
    owner.post(f"/dashboard/users/{bob['id']}/delete")
    assert any(m[0] == "jane@example.com" and "approved" in m[2] for m in sent)
    assert any(m[0] == "bob@example.com" and "unable to approve" in m[2] for m in sent)


def _reset_link(sent):
    import re
    body = next(m[2] for m in sent if "reset your password" in m[2])
    return re.search(r"http\S*(/dashboard/reset/\S+)", body).group(1)


def test_password_reset_flow_is_single_use(env, sent):
    _register(env())
    sent.clear()
    client = env()
    assert client.post("/dashboard/forgot", data={"email": "JANE@example.com"}).status_code == 200
    path = _reset_link(sent)
    assert client.get(path).status_code == 200
    assert client.post(path, data={"password": "short", "confirm_password": "short"}).status_code == 400
    assert client.post(path, data={"password": "brand-new-pass1", "confirm_password": "other"}).status_code == 400
    assert client.post(path, data={"password": "brand-new-pass1", "confirm_password": "brand-new-pass1"}).status_code == 302
    assert client.get(path).status_code == 400
    assert env().post("/dashboard/login", data={"username": "jane@example.com", "password": "brand-new-pass1"}).status_code == 302


def test_owner_can_send_reset_link_to_user(env, sent):
    _register(env())
    jane = users.list_users()[0]
    sent.clear()
    assert env().post(f"/dashboard/users/{jane['id']}/send-reset").status_code in (401, 403)
    assert sent == []

    owner = env()
    owner.post("/dashboard/login", data={"username": "owner", "password": "owner-pw-123"})
    resp = owner.post(f"/dashboard/users/{jane['id']}/send-reset")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("reset_sent=1")
    path = _reset_link(sent)
    assert sent[-1][0] == "jane@example.com"
    assert env().get(path).status_code == 200
    assert owner.post("/dashboard/users/missing/send-reset").headers["Location"].endswith("reset_sent=0")

def test_forgot_does_not_reveal_unknown_accounts(env, sent):
    client = env()
    known = client.post("/dashboard/forgot", data={"email": "nobody@example.com"})
    assert known.status_code == 200 and "reset link has been sent" in known.get_data(as_text=True)
    assert sent == []


def test_invalid_and_expired_reset_tokens(env, sent, monkeypatch):
    _register(env())
    client = env()
    assert client.get("/dashboard/reset/garbage").status_code == 400
    sent.clear()
    client.post("/dashboard/forgot", data={"email": "jane@example.com"})
    path = _reset_link(sent)
    monkeypatch.setattr(dashboard_module, "RESET_TOKEN_MAX_AGE_SECONDS", -1)
    assert client.get(path).status_code == 400


@pytest.fixture
def snapshot_path(tmp_path, monkeypatch):
    import public_snapshot
    path = str(tmp_path / "public_snapshot.json")
    monkeypatch.setattr(public_snapshot, "SNAPSHOT_PATH", path)
    public_snapshot._cache.update(ts=0.0, path=None, data=None)
    return path


def test_landing_page_is_public_with_risk_warning_and_register_cta(env, snapshot_path):
    resp = env().get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Capital at risk" in html
    assert "/dashboard/register" in html
    assert "Performance figures will appear here" in html
    # anonymous visitors only get public navigation
    assert 'href="/dashboard/trades"' not in html and 'href="/dashboard/roi"' not in html


def test_landing_page_shows_only_percentages_from_snapshot(env, snapshot_path):
    import public_snapshot
    public_snapshot.write_snapshot({
        "last_week": {"label": "05/10/26 - 09/10/26", "return_pct": 6.0},
        "last_month": {"label": "September 2026", "return_pct": -1.5},
        "hit_rate": {"successful_weeks": 26, "total_weeks": 52, "pct": 50, "target_pct": 5.0},
        "avg_weekly_return": 3.25,
    })
    html = env().get("/").get_data(as_text=True)
    assert "+6.00%" in html and "-1.50%" in html
    assert "26 of 52" in html and "50% of weeks" in html
    assert "+3.25%" in html
    assert "£" not in html


def test_landing_page_adapts_for_logged_in_users(env, snapshot_path):
    client = env()
    _register(client)
    assert "View registration status" in client.get("/").get_data(as_text=True)
    _approve("jane@example.com")
    html = client.get("/").get_data(as_text=True)
    assert "Go to dashboard" in html and "Register your interest" not in html


def test_snapshot_job_writes_percentages_only(env, snapshot_path, monkeypatch):
    import public_snapshot
    monkeypatch.setattr(dashboard_module, "load_raw_log", lambda: [])
    dashboard_module.refresh_public_snapshot()
    data = public_snapshot.load_snapshot()
    assert set(data) == {"generated_at", "last_week", "last_month", "hit_rate", "avg_weekly_return"}


def test_login_and_register_pages_link_to_legal_pages(env):
    client = env()
    for path in ("/dashboard/login", "/dashboard/register", "/dashboard/forgot"):
        html = client.get(path).get_data(as_text=True)
        assert "/dashboard/terms" in html and "/dashboard/faq" in html and "/dashboard/contact" in html, path


@pytest.fixture
def investor_env(env, tmp_path, monkeypatch):
    import deposits
    monkeypatch.setattr(deposits, "PLEDGES_PATH", str(tmp_path / "pledges.json"))
    monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))
    entries = []
    monkeypatch.setattr(deposits, "list_entries_sorted", lambda: entries)
    monkeypatch.setattr(deposits, "list_pledges_sorted", lambda path=str(tmp_path / "pledges.json"): deposits.load_pledges(path))
    return env, entries


def test_become_investor_prefills_name_and_records_tier(investor_env, tmp_path):
    import deposits
    env, _entries = investor_env
    client = env()
    _register(client)
    _approve("jane@example.com")
    html = client.get("/dashboard/investors").get_data(as_text=True)
    assert "Become an Investor" in html and "Make a deposit" not in html
    assert 'value="Jane Doe"' in html and "TradingView only" in html and "Full Investor" in html

    resp = client.post("/dashboard/investors/pledge",
                       json={"investor": "Someone Else", "amount": "100", "tier": "full"})
    assert resp.status_code == 200
    pledge = resp.get_json()["pledge"]
    assert pledge["investor"] == "Jane Doe" and pledge["tier"] == "full"

    deposits.set_pledge_status(pledge["id"], "confirmed")
    assert deposits.get_investor_tier("Jane Doe") == "full"


def test_existing_investor_sees_make_a_deposit(investor_env):
    env, entries = investor_env
    client = env()
    _register(client)
    _approve("jane@example.com", role="investor")
    entries.append({"type": "deposit", "investor": "Jane Doe", "amount": 100.0})
    html = client.get("/dashboard/investors").get_data(as_text=True)
    assert "Make a deposit" in html and "Become an Investor" not in html
    assert 'id="pledge-tier"' not in html


def test_viewer_open_positions_show_side(env, monkeypatch):
    client = env()
    _register(client)
    _approve("jane@example.com")
    monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
        "account": {"equity": 1, "balance": 1, "pnl": 0, "available": 1},
        "positions": [{"ticker": "AAPL", "direction": "Short"}],
        "combined_trades": [], "analytics": dashboard_module._safe_analytics({}),
        "weekly_analytics": dashboard_module._safe_analytics({}),
        "monthly_analytics": dashboard_module._safe_analytics({}),
    })
    html = client.get("/dashboard/data").get_data(as_text=True)
    assert "AAPL" in html and "pill-short" in html and "text-align:center" in html
