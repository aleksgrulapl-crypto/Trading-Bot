# ============================
# AUTH MODULE (Rewritten)
# ============================

import requests
import time
from config import (
    API_LOGIN,
    API_REFRESH,
    CAPITAL_API_KEY,
    CAPITAL_USERNAME,
    CAPITAL_PASSWORD
)

class CapitalAuth:
    def __init__(self):
        self.cst = None
        self.xst = None
        self.last_login = 0
        self.session = requests.Session()

    # ---------------------------------------------------------
    # FULL LOGIN (always restores full metadata permissions)
    # ---------------------------------------------------------
    def login(self):
        print("Performing FULL login...")

        payload = {
            "identifier": CAPITAL_USERNAME,
            "password": CAPITAL_PASSWORD
        }

        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "Content-Type": "application/json"
        }

        r = self.session.post(API_LOGIN, json=payload, headers=headers)

        if r.status_code != 200:
            raise Exception(f"Login failed: {r.text}")

        self.cst = r.headers.get("CST")
        self.xst = r.headers.get("X-SECURITY-TOKEN")
        self.last_login = time.time()

        print("FULL login successful. CST/XST refreshed.")

    # ---------------------------------------------------------
    # SAFE REFRESH (fallback to full login if metadata degrades)
    # ---------------------------------------------------------
    def refresh(self):
        print("Attempting token refresh...")

        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        }

        r = self.session.post(API_REFRESH, headers=headers)

        # If refresh fails → full login
        if r.status_code != 200:
            print("Refresh failed → performing FULL login")
            return self.login()

        # If refresh succeeds → update tokens
        self.cst = r.headers.get("CST")
        self.xst = r.headers.get("X-SECURITY-TOKEN")
        self.last_login = time.time()

        print("Token refreshed successfully.")

    # ---------------------------------------------------------
    # TOKEN MANAGEMENT (aggressive refresh to prevent metadata loss)
    # ---------------------------------------------------------
    def ensure_token(self):
        # If no token → login
        if self.cst is None or self.xst is None:
            print("No token found → FULL login")
            return self.login()

        # Force full login every 10 minutes (prevents metadata downgrade)
        if time.time() - self.last_login > 600:
            print("Token older than 10 minutes → FULL login")
            return self.login()

    # ---------------------------------------------------------
    # REQUEST WRAPPER (auto-relogin on metadata loss)
    # ---------------------------------------------------------
    def request(self, method, url, **kwargs):
        self.ensure_token()

        headers = kwargs.pop("headers", {})
        headers.update({
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        })

        r = self.session.request(method, url, headers=headers, **kwargs)

        # If unauthorized → refresh then retry
        if r.status_code == 401:
            print("401 detected → refreshing token")
            self.refresh()

            headers.update({
                "CST": self.cst,
                "X-SECURITY-TOKEN": self.xst
            })

            r = self.session.request(method, url, headers=headers, **kwargs)

        return r


# ---------------------------------------------------------
# GLOBAL AUTH INSTANCE
# ---------------------------------------------------------

auth = CapitalAuth()
