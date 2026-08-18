# ============================
# MARKET MODULE (FINAL + STABLE + CACHED)
# ============================

import time
from auth import auth
from config import API_MARKET
import requests

# ---------------------------------------------------------
# AUTHENTICATED REQUEST WRAPPER
# ---------------------------------------------------------

def request(method, url, **kwargs):
    """
    Unified authenticated request wrapper.
    Uses the same token + API key as the rest of the backend.
    """

    auth.ensure_token()

    headers = kwargs.pop("headers", {})
    headers.update({
        "X-CAP-API-KEY": auth.api_key,
        "CST": auth.cst,
        "X-SECURITY-TOKEN": auth.xst
    })

    try:
        return auth.session.request(method, url, headers=headers, **kwargs)
    except Exception as e:
        print(f"[MARKET] Request error: {e}")
        return None


# ---------------------------------------------------------
# MARKET DATA CLASS (CACHED)
# ---------------------------------------------------------

class MarketData:
    """
    Provides:
    - instrument lookup
    - bid/offer snapshot
    - midpoint price
    - entry price for SL/TP modules
    """

    cache = {}
    cache_ttl = 2  # seconds

    @staticmethod
    def _fetch(epic):
        """
        Fetches fresh market data from Capital.com.
        """

        if not epic:
            return {}

        try:
            r = request("GET", f"{API_MARKET}/{epic}")
            if not r or r.status_code != 200:
                print(f"[MARKET] Snapshot failed for {epic}")
                return {}

            return r.json()

        except Exception as e:
            print(f"[MARKET] Snapshot exception for {epic}: {e}")
            return {}

    @staticmethod
    def get_snapshot(epic):
        """
        Cached market snapshot.
        """

        now = time.time()
        cached = MarketData.cache.get(epic)

        if cached and now - cached["time"] < MarketData.cache_ttl:
            return cached["data"]

        data = MarketData._fetch(epic)
        MarketData.cache[epic] = {"time": now, "data": data}
        return data

    @staticmethod
    def get_bid_offer(epic):
        """
        Returns (bid, offer) safely.
        """

        snap = MarketData.get_snapshot(epic)
        snapshot = snap.get("snapshot", {}) if isinstance(snap, dict) else {}

        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if bid is None or offer is None:
            return None, None

        return float(bid), float(offer)

    @staticmethod
    def get_midpoint(epic):
        """
        Returns midpoint price.
        """

        bid, offer = MarketData.get_bid_offer(epic)
        if bid is None or offer is None:
            return None

        return (bid + offer) / 2

    @staticmethod
    def get_entry_price(ticker, side):
        """
        Used by SL/TP module.
        Converts ticker → epic → midpoint.
        """

        from session import verify_epic
        epic_data = verify_epic(ticker)
        epic = epic_data.get("epic")

        if not epic:
            print(f"[MARKET] No EPIC for ticker {ticker}")
            return None

        midpoint = MarketData.get_midpoint(epic)
        if midpoint is None:
            print(f"[MARKET] No midpoint for epic {epic}")
            return None

        return midpoint


# ---------------------------------------------------------
# INSTRUMENT LOOKUP (RAW)
# ---------------------------------------------------------

def get_instrument(epic):
    """
    Raw instrument metadata lookup.
    """

    if not epic:
        return {}

    try:
        r = request("GET", f"{API_MARKET}/{epic}")
        if not r or r.status_code != 200:
            return {}

        return r.json()

    except Exception as e:
        print("Instrument lookup error:", e)
        return {}