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
        self.session.timeout = 10  # global safety timeout
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
        """
        Ensures a valid token is present.
        Refreshes if:
        - missing
        - older than 10 minutes
        """

        # No token at all
        if not self.cst or not self.xst:
            print("[AUTH] No token found → login")
            return self.login()

        # Token expired
        if time.time() - self.last_login > 600:
            print("[AUTH] Token expired → login")
            return self.login()

    # ---------------------------------------------------------
    # REQUEST WRAPPER
    # ---------------------------------------------------------
    def request(self, method, url, **kwargs):
        """
        Unified request wrapper:
        - ensures token
        - retries on 401/403
        - safe header rebuild
        """

        self.ensure_token()

        headers = kwargs.pop("headers", {})
        headers.update({
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        })

        try:
            r = self.session.request(method, url, headers=headers, **kwargs)

            # 401 → token invalid → re-login
            if r.status_code == 401:
                print("[AUTH] 401 detected → re-login")
                self.login()

                headers.update({
                    "CST": self.cst,
                    "X-SECURITY-TOKEN": self.xst
                })

                r = self.session.request(method, url, headers=headers, **kwargs)

            # 403 → token expired or invalid
            if r.status_code == 403:
                print("[AUTH] 403 detected → re-login")
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
