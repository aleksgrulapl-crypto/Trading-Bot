import dashboard as dm
from flask import Flask
import users


def _client(tmp_path, monkeypatch, role="owner", uid=None):
    monkeypatch.setattr(users, "USERS_PATH", str(tmp_path / "u.json"))
    app = Flask(__name__, template_folder="templates")
    app.secret_key = "t"
    app.register_blueprint(dm.dashboard)
    c = app.test_client()
    with c.session_transaction() as s:
        s["role"] = role
        if uid:
            s["uid"] = uid
    return c


def test_settings_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_PATH", str(tmp_path / "u.json"))
    _, user, _ = users.register("a@b.co", "password123", "Alice")
    users.set_role(user["id"], "investor")
    inv = _client(tmp_path, monkeypatch, "investor", user["id"])
    r0 = inv.get("/dashboard/settings")
    assert r0.status_code == 200 and b"Appearance" in r0.data and b"Account details" not in r0.data
    assert inv.post("/dashboard/settings/email", data={"email": "q@w.co", "current_password": "x"}).status_code == 302
    assert users.get_user(user["id"])["email"] == "a@b.co"
    monkeypatch.setattr(dm, "_page_account", lambda: {"pnl": 0})
    owner = _client(tmp_path, monkeypatch, "owner")
    r = owner.get("/dashboard/settings")
    assert r.status_code == 200 and b"Appearance" in r.data and b"/dashboard/settings" in r.data


def test_change_email_and_password(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_PATH", str(tmp_path / "u.json"))
    _, user, _ = users.register("a@b.co", "password123", "Alice")
    assert users.change_email(user["id"], "bad", "x@y.co") == (False, "wrong_password")
    assert users.change_email(user["id"], "password123", "X@Y.co") == (True, "ok")
    assert users.get_user(user["id"])["email"] == "x@y.co"
    assert users.change_password(user["id"], "password123", "short")[1] == "password_too_short"
    assert users.change_password(user["id"], "password123", "newpassword9") == (True, "ok")
    assert users.authenticate("x@y.co", "newpassword9")
