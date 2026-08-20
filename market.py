# market.py
# ============================
# MARKET MODULE (FINAL + STABLE + CACHED)
# ============================

import time
import logging
from typing import Optional, Tuple

import session
import config

logger = logging.getLogger("market")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [market] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)


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

    _snapshot_cache = {}     # epic -> {"time": ts, "data": {...}}
    _instrument_cache = {}   # epic -> {"time": ts, "data": {...}}
    cache_ttl = float(getattr(config, "CACHE_TTL_SECONDS", 2))

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _fetch_raw(epic: str) -> dict:
        """
        Fetch fresh market data from Capital.com via session.request.
        Returns parsed JSON dict or {} on failure.
        """
        if not epic:
            return {}

        try:
            url = f"{config.API_MARKET}/{epic}"
            r = session.request("GET", url)
            if not r or r.status_code != 200:
                logger.debug("Market snapshot request failed for %s: %s", epic, getattr(r, "status_code", "no_response"))
                return {}
            try:
                return r.json() or {}
            except Exception:
                logger.debug("Market snapshot JSON parse failed for %s", epic)
                return {}
        except Exception as e:
            logger.exception("Exception fetching market data for %s: %s", epic, e)
            return {}

    @classmethod
    def get_snapshot(cls, epic: str) -> dict:
        """
        Cached market snapshot JSON (the full response body).
        """
        if not epic:
            return {}

        now = cls._now()
        cached = cls._snapshot_cache.get(epic)
        if cached and now - cached["time"] < cls.cache_ttl:
            return cached["data"]

        data = cls._fetch_raw(epic)
        cls._snapshot_cache[epic] = {"time": now, "data": data}
        return data

    @classmethod
    def get_bid_offer(cls, epic: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Returns (bid, offer) as floats or (None, None) if unavailable.
        """
        snap = cls.get_snapshot(epic)
        if not isinstance(snap, dict):
            return None, None
        snapshot = snap.get("snapshot", {}) or {}
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")
        try:
            bid_f = float(bid) if bid is not None else None
        except Exception:
            bid_f = None
        try:
            offer_f = float(offer) if offer is not None else None
        except Exception:
            offer_f = None
        if bid_f is None or offer_f is None:
            return None, None
        return bid_f, offer_f

    @classmethod
    def get_midpoint(cls, epic: str) -> Optional[float]:
        """
        Returns midpoint price or None.
        """
        bid, offer = cls.get_bid_offer(epic)
        if bid is None or offer is None:
            return None
        return (bid + offer) / 2.0

    @classmethod
    def get_entry_price(cls, ticker: str, side: Optional[str] = None) -> Optional[float]:
        """
        Used by SL/TP module.
        Converts ticker → epic → midpoint.
        """
        from session import verify_epic
        epic_info = verify_epic(ticker)
        epic = epic_info.get("epic")
        if not epic:
            logger.debug("get_entry_price: no epic for ticker %s", ticker)
            return None
        midpoint = cls.get_midpoint(epic)
        if midpoint is None:
            logger.debug("get_entry_price: no midpoint for epic %s", epic)
            return None
        return midpoint

    @classmethod
    def get_instrument(cls, epic: str) -> dict:
        """
        Cached instrument metadata lookup.
        """
        if not epic:
            return {}

        now = cls._now()
        cached = cls._instrument_cache.get(epic)
        if cached and now - cached["time"] < cls.cache_ttl:
            return cached["data"]

        data = cls._fetch_raw(epic)
        cls._instrument_cache[epic] = {"time": now, "data": data}
        return data


# ---------------------------------------------------------
# Convenience functions (backwards-compatible)
# ---------------------------------------------------------

def request(method: str, url: str, **kwargs):
    """
    Lightweight wrapper that delegates to session.request for authenticated calls.
    Kept for compatibility with older modules that import market.request.
    """
    return session.request(method, url, **kwargs)


def get_snapshot(epic: str) -> dict:
    return MarketData.get_snapshot(epic)


def get_bid_offer(epic: str) -> Tuple[Optional[float], Optional[float]]:
    return MarketData.get_bid_offer(epic)


def get_midpoint(epic: str) -> Optional[float]:
    return MarketData.get_midpoint(epic)


def get_entry_price(ticker: str, side: Optional[str] = None) -> Optional[float]:
    return MarketData.get_entry_price(ticker, side)


def get_instrument(epic: str) -> dict:
    return MarketData.get_instrument(epic)
