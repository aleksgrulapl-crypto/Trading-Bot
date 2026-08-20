# auth.py
# ============================
# AUTH MODULE (FINAL — STABLE + SAFE + UNIFIED)
# ============================

import time
import logging
from typing import Optional

import requests
from requests import Response
from requests.exceptions import RequestException

from config import (
    API_LOGIN,
    CAPITAL_API_KEY,
    CAPITAL_USERNAME,
    CAPITAL_PASSWORD,
)

# Configure module logger
logger = logging.getLogger("auth")
if not logger.handlers:
    # Avoid reconfiguring logging if already configured by app
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [auth] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class CapitalAuth:
    """
    Handles:
      - Login with retries and backoff
      - Token storage (CST, XST)
      - Token refresh via re-login
      - Safe request wrapper that injects headers
      - Minimal, non-sensitive logging
    """

    def __init__(self, timeout: float = 10.0, login_retries: int = 3, login_backoff: float = 0.5):
        self.cst: Optional[str] = None
        self.xst: Optional[str] = None
        self.last_login: float = 0.0
        self.session = requests.Session()
        self.timeout = timeout
        self.api_key = CAPITAL_API_KEY
        self.login_retries = max(1, int(login_retries))
        self.login_backoff = float(login_backoff)

    # ---------------------------------------------------------
    # INTERNAL: perform a single login attempt
    # ---------------------------------------------------------
    def _do_login(self) -> bool:
        """
        Perform a single login attempt. Returns True on success.
        Does not log sensitive values.
        """
        payload = {
            "identifier": CAPITAL_USERNAME,
            "password": CAPITAL_PASSWORD,
        }

        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        try:
            resp: Response = self.session.post(API_LOGIN, json=payload, headers=headers, timeout=self.timeout)
        except RequestException as exc:
            logger.debug("Login request exception: %s", exc)
            return False

        if resp is None:
            logger.debug("Login: no response object")
            return False

        if resp.status_code != 200:
            # Log status and short body for debugging but avoid printing credentials
            body_snippet = (resp.text or "")[:400]
            logger.warning("Login failed: status=%s body=%s", resp.status_code, body_snippet)
            return False

        # Extract tokens from headers
        self.cst = resp.headers.get("CST")
        self.xst = resp.headers.get("X-SECURITY-TOKEN")
        self.last_login = time.time()

        if not self.cst or not self.xst:
            logger.warning("Login response missing CST/X-SECURITY-TOKEN headers")
            # Clear any partial tokens
            self.cst = None
            self.xst = None
            return False

        logger.info("Login successful")
        return True

    # ---------------------------------------------------------
    # PUBLIC: login with retries and backoff
    # ---------------------------------------------------------
    def login(self) -> bool:
        """
        Attempt login with configured retries and exponential backoff.
        Returns True on success, False on failure.
        """
        backoff = self.login_backoff
        for attempt in range(1, self.login_retries + 1):
            logger.debug("Login attempt %d/%d", attempt, self.login_retries)
            ok = self._do_login()
            if ok:
                return True
            if attempt < self.login_retries:
                logger.debug("Login retry in %.2fs", backoff)
                time.sleep(backoff)
                backoff *= 2.0
        logger.error("Login failed after %d attempts", self.login_retries)
        return False

    # ---------------------------------------------------------
    # PUBLIC: ensure token available and fresh
    # ---------------------------------------------------------
    def ensure_token(self) -> bool:
        """
        Ensure we have valid tokens. Returns True if tokens are present (and fresh),
        False if login failed.
        """
        # If tokens missing, attempt login
        if not self.cst or not self.xst:
            logger.debug("No tokens present, attempting login")
            return self.login()

        # If token older than 10 minutes, refresh
        if time.time() - self.last_login > 600:
            logger.debug("Token expired, attempting re-login")
            return self.login()

        return True

    # ---------------------------------------------------------
    # PUBLIC: clear tokens (safe)
    # ---------------------------------------------------------
    def clear_tokens(self) -> None:
        """Clear stored tokens and last_login timestamp."""
        self.cst = None
        self.xst = None
        self.last_login = 0.0
        logger.debug("Cleared auth tokens")

    # ---------------------------------------------------------
    # REQUEST WRAPPER
    # ---------------------------------------------------------
    def request(self, method: str, url: str, **kwargs) -> Optional[Response]:
        """
        Wrapper around requests.Session.request that ensures tokens are present
        and injects required headers. Returns Response or None on failure.
        """
        # Ensure tokens; if ensure_token fails, return None
        if not self.ensure_token():
            logger.error("Cannot perform request: authentication failed")
            return None

        headers = kwargs.pop("headers", {}) or {}
        headers.update({
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst,
        })

        # Ensure timeout is set unless caller provided one
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        try:
            resp = self.session.request(method, url, headers=headers, **kwargs)
        except RequestException as exc:
            logger.warning("Request exception for %s %s: %s", method, url, exc)
            return None

        # If unauthorized, try one re-login and retry once
        if resp is not None and resp.status_code in (401, 403):
            logger.info("Received %s from API, attempting re-login and retry", resp.status_code)
            if not self.login():
                logger.error("Re-login failed after unauthorized response")
                return resp
            # update headers with new tokens and retry
            headers.update({
                "CST": self.cst,
                "X-SECURITY-TOKEN": self.xst,
            })
            try:
                resp = self.session.request(method, url, headers=headers, **kwargs)
            except RequestException as exc:
                logger.warning("Retry request exception for %s %s: %s", method, url, exc)
                return None

        return resp


# ---------------------------------------------------------
# GLOBAL AUTH INSTANCE
# ---------------------------------------------------------
auth = CapitalAuth()
