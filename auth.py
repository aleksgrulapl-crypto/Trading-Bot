# ============================
# AUTH MODULE (FINAL VERSION)
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

    # ---------------------------------------------------------
    # FULL LOGIN (always used — refresh-token removed)
    # ---------------------------------------------------------
    def login(self):
        print("\n" + "="*60)
        print("[AUTH] Performing FULL login...")
        print(f"[AUTH] Endpoint: {API_LOGIN}")
        print("="*60)

        payload = {
            "identifier": CAPITAL_USERNAME,
            "password": CAPITAL_PASSWORD
        }

        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "Content-Type": "application/json"
        }

        r = self.session.post(API_LOGIN, json=payload, headers=headers)

        print(f"[AUTH] Status: {r.status_code}")
        print(f"[AUTH] Raw response: {r.text}")

        if r.status_code != 200:
            raise Exception(f"[AUTH] Login failed: {r.text}")

        self.cst = r.headers.get("CST")
        self.xst = r.headers.get("X-SECURITY-TOKEN")
        self.last_login = time.time()

        print(f"[AUTH] CST: {self.cst}")
        print(f"[AUTH] XST: {self.xst}")
        print("[AUTH] FULL login successful.")
        print("="*60 + "\n")

    # ---------------------------------------------------------
    # TOKEN MANAGEMENT (refresh-token removed)
    # ---------------------------------------------------------
    def ensure_token(self):
        # No token → login
        if not self.cst or not self.xst:
            print("[AUTH] No token found → FULL login")
            return self.login()

        # Token older than 10 minutes → login again
        if time.time() - self.last_login > 600:
            print("[AUTH] Token older than 10 minutes → FULL login")
            return self.login()

    # ---------------------------------------------------------
    # REQUEST WRAPPER (401 retry → full login)
    # ---------------------------------------------------------
    def request(self, method, url, **kwargs):
        self.ensure_token()

        headers = kwargs.pop("headers", {})
        headers.update({
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        })

        print("\n" + "="*60)
        print(f"[AUTH REQUEST] {method} {url}")
        print(f"[AUTH REQUEST] Headers: {headers}")
        print("="*60)

        r = self.session.request(method, url, headers=headers, **kwargs)

        print(f"[AUTH REQUEST] Status: {r.status_code}")
        print(f"[AUTH REQUEST] Raw response: {r.text}")
        print("="*60 + "\n")

        # -----------------------------------------------------
        # 401 → FULL LOGIN → RETRY
        # -----------------------------------------------------
        if r.status_code == 401:
            print("[AUTH REQUEST] 401 detected → FULL login")
            self.login()

            headers.update({
                "CST": self.cst,
                "X-SECURITY-TOKEN": self.xst
            })

            r = self.session.request(method, url, headers=headers, **kwargs)

            print("[AUTH REQUEST] Retried request:")
            print(f"[AUTH REQUEST] Status: {r.status_code}")
            print(f"[AUTH REQUEST] Raw response: {r.text}")
            print("="*60 + "\n")

        return r


# ---------------------------------------------------------
# GLOBAL AUTH INSTANCE
# ---------------------------------------------------------

auth = CapitalAuth()
