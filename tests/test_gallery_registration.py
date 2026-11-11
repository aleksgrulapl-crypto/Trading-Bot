import io

import pytest
from flask import Flask

import dashboard
import ui_prefs

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _client(role=None):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard.dashboard)
    c = app.test_client()
    if role:
        c.set_cookie("dashboard_auth", "1")
        c.set_cookie("dashboard_role", role)
    return c


@pytest.fixture(autouse=True)
def _reset():
    ui_prefs.set_registration_open(True)
    yield
    ui_prefs.set_registration_open(True)


def test_registration_toggle_hides_buttons_and_blocks_signup():
    assert "Register your interest" in _client().get("/").get_data(as_text=True)
    assert _client("owner").post("/dashboard/registration/toggle", json={"enabled": False}).status_code == 200
    anon = _client()
    assert "Register your interest" not in anon.get("/").get_data(as_text=True)
    assert anon.get("/dashboard/register").status_code == 302
    assert anon.post("/dashboard/register", data={"email": "a@b.co"}).status_code == 302


def test_registration_toggle_is_owner_only():
    assert _client("investor").post("/dashboard/registration/toggle", json={"enabled": False}).status_code == 403
    assert ui_prefs.registration_open()


def test_gallery_owner_upload_and_view():
    owner = _client("owner")
    r = owner.post("/dashboard/gallery/upload", data={"image": (io.BytesIO(PNG), "t.png"), "caption": "Big win"},
                   content_type="multipart/form-data")
    assert "msg=uploaded" in r.headers["Location"]
    html = _client("viewer").get("/dashboard/gallery").get_data(as_text=True)
    assert "Big win" in html and "/dashboard/gallery/upload" not in html
    name = dashboard.gallery.list_items()[0]["file"]
    assert _client("viewer").get(f"/dashboard/gallery/img/{name}").data == PNG
    assert _client("viewer").get("/dashboard/gallery/img/..%2Fx.png").status_code == 404


def test_gallery_rejects_non_owner_and_non_images():
    viewer = _client("investor")
    r = viewer.post("/dashboard/gallery/upload", data={"image": (io.BytesIO(PNG), "t.png")},
                    content_type="multipart/form-data")
    assert r.headers["Location"] == "/dashboard"
    r = _client("owner").post("/dashboard/gallery/upload", data={"image": (io.BytesIO(b"<svg/>"), "x.svg")},
                              content_type="multipart/form-data")
    assert "msg=bad_type" in r.headers["Location"]
