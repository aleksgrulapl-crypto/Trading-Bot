# ============================
# AUTH MODULE (Debug Version)
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
    # FULL LOGIN
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
    # REFRESH TOKEN
    # ---------------------------------------------------------
    def refresh(self):
        print("\n" + "="*60)
        print("[AUTH] Attempting token refresh...")
        print(f"[AUTH] Endpoint: {API_REFRESH}")
        print("="*60)

        headers = {
            "X-CAP-API-KEY": CAPITAL_API_KEY,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst
        }

        r = self.session.post(API_REFRESH, headers=headers)

        print(f"[AUTH] Status: {r.status_code}")
        print(f"[AUTH] Raw response: {r.text}")

        if r.status_code != 200:
            print("[AUTH] Refresh failed → FULL login required")
            return self.login()

        self.cst = r.headers.get("CST")
        self.xst = r.headers.get("X-SECURITY-TOKEN")
        self.last_login = time.time()

        print(f"[AUTH] New CST: {self.cst}")
        print(f"[AUTH] New XST: {self.xst}")
        print("[AUTH] Token refreshed successfully.")
        print("="*60 + "\n")

    # ---------------------------------------------------------
    # TOKEN MANAGEMENT
    # ---------------------------------------------------------
    def ensure_token(self):
        if not self.cst or not self.xst:
            print("[AUTH] No token found → FULL login")
            return self.login()

        # Refresh every 20 minutes (safer)
        if time.time() - self.last_login > 1200:
            print("[AUTH] Token older than 20 minutes → refreshing")
            return self.refresh()

    # ---------------------------------------------------------
    # REQUEST WRAPPER
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

        if r.status_code == 401:
            print("[AUTH REQUEST] 401 detected → refreshing token")
            self.refresh()

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
