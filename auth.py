# ============================
# AUTH MODULE (FINAL — STABLE + SAFE + UNIFIED)
# ============================

import requests
import time
from config import (
    API_LOGIN,
    CAPITAL_API_KEY,
    CAPITAL_USERNAME,
    CAPITAL_PASSWORD
)


class CapitalAuth:
    """
    Handles:
    - Login
    - Token storage
    - Token refresh
    - Safe retry logic
    - Unified session headers
    """

    def __init__(self):
        self.cst = None
        self.xst = None
        self.last_login = 0
        self.session = requests.Session()
        self.session.timeout = 10
        self.api_key = CAPITAL_API_KEY

    # ---------------------------------------------------------
    # FULL LOGIN
    # ---------------------------------------------------------
    def login(self):
        payload = {
            "identifier": CAPITAL_USERNAME,
            "password": CAPITAL_PASSWORD
        }

        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }

        try:
            r = self.session.post(API_LOGIN, json=payload, headers=headers)

            if r.status_code != 200:
                print(f"[AUTH ERROR] Login failed → {r.text}")
                raise Exception("Login failed")

            self.cst = r.headers.get("CST")
            self.xst = r.headers.get("X-SECURITY-TOKEN")
            self.last_login = time.time()

            print("[AUTH] Login successful.")

        except Exception as e:
            print(f"[AUTH ERROR] Exception during login: {e}")
            raise

    # ---------------------------------------------------------
    # TOKEN MANAGEMENT
    # ---------------------------------------------------------
    def ensure_token(self):
        if not self.cst or not self.xst:
            print("[AUTH] No token found → login")
            return self.login()

        if time.time() - self.last_login > 600:
            print("[AUTH] Token expired → login")
            return self.login()

    # ---------------------------------------------------------
    # REQUEST WRAPPER
    # ---------------------------------------------------------
    def request(self, method, url, **kwargs):
        self.ensure_token()

        headers = kwargs.pop("headers", {})
        headers.update({
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        })

        try:
            r = self.session.request(method, url, headers=headers, **kwargs)

            if r.status_code in (401, 403):
                print(f"[AUTH] {r.status_code} detected → re-login")
                self.login()

                headers.update({
                    "CST": self.cst,
                    "X-SECURITY-TOKEN": self.xst
                })

                r = self.session.request(method, url, headers=headers, **kwargs)

            return r

        except Exception as e:
            print(f"[AUTH ERROR] Request failed: {e}")
            return None


# ---------------------------------------------------------
# GLOBAL AUTH INSTANCE
# ---------------------------------------------------------

auth = CapitalAuth()
