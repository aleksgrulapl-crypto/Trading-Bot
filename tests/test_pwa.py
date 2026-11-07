import webhook

def test_pwa_assets_served():
    c = webhook.app.test_client()
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.get_json()["display"] == "standalone"
    s = c.get("/sw.js")
    assert s.status_code == 200 and "javascript" in s.mimetype
    assert c.get("/static/icons/icon-512.png").status_code == 200
    assert c.get("/static/offline.html").status_code == 200
