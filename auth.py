# ============================
# AUTH MODULE (FINAL CLEAN)
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
    def __init__(self):
        self.cst = None
        self.xst = None
        self.last_login = 0
        self.session = requests.Session()
        self.api_key = CAPITAL_API_KEY

    # ---------------------------------------------------------
    # FULL LOGIN (silent, clean)
    # ---------------------------------------------------------
    def login(self):
        payload = {
            "identifier": CAPITAL_USERNAME,
            "password": CAPITAL_PASSWORD
        }

        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
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
        # No token → login
        if not self.cst or not self.xst:
            print("[AUTH] No token found → login")
            return self.login()

        # Token older than 10 minutes → login again
        if time.time() - self.last_login > 600:
            print("[AUTH] Token expired → login")
            return self.login()

    # ---------------------------------------------------------
    # REQUEST WRAPPER (silent, clean)
    # ---------------------------------------------------------
    def request(self, method, url, **kwargs):
        self.ensure_token()

        headers = kwargs.pop("headers", {})
        headers.update({
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        })

        try:
            r = self.session.request(method, url, headers=headers, **kwargs)

            # 401 → login → retry
            if r.status_code == 401:
                print("[AUTH] 401 detected → re-login")
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
