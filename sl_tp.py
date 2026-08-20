# fixed_sltp.py
# ============================
# FIXED SL/TP MODULE (Module 5 — Corrected + Stable)
# ============================

import logging
from typing import Optional, Tuple

import config
from market import MarketData

logger = logging.getLogger("fixed_sltp")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [fixed_sltp] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)


class FixedSLTP:
    """
    Fixed SL/TP logic.
    - Uses configured FIXED_SL_PERC and FIXED_TP_PERC from config.
    - Returns (sl, tp) as rounded floats or (None, None) if entry price unavailable.
    """

    @staticmethod
    def long_levels(entry_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Long trade:
          SL = entry * (1 - FIXED_SL_PERC)
          TP = entry * (1 + FIXED_TP_PERC)
        """
        try:
            if entry_price is None:
                return None, None
            sl = float(entry_price) * (1.0 - float(getattr(config, "FIXED_SL_PERC", 0.10)))
            tp = float(entry_price) * (1.0 + float(getattr(config, "FIXED_TP_PERC", 0.20)))
            return round(sl, 2), round(tp, 2)
        except Exception as e:
            logger.exception("long_levels error: %s", e)
            return None, None

    @staticmethod
    def short_levels(entry_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Short trade:
          SL = entry * (1 + FIXED_SL_PERC)
          TP = entry * (1 - FIXED_TP_PERC)
        """
        try:
            if entry_price is None:
                return None, None
            sl = float(entry_price) * (1.0 + float(getattr(config, "FIXED_SL_PERC", 0.10)))
            tp = float(entry_price) * (1.0 - float(getattr(config, "FIXED_TP_PERC", 0.20)))
            return round(sl, 2), round(tp, 2)
        except Exception as e:
            logger.exception("short_levels error: %s", e)
            return None, None

    @staticmethod
    def get_levels(ticker: str, side: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Main wrapper:
          - Resolves an entry price via MarketData.get_entry_price(ticker, side)
          - Normalizes side and returns (sl, tp)
          - Returns (None, None) if entry price or side invalid
        """
        if not ticker:
            logger.debug("get_levels: missing ticker")
            return None, None

        if not side:
            logger.debug("get_levels: missing side")
            return None, None

        side_norm = str(side).strip().lower()
        try:
            entry_price = MarketData.get_entry_price(ticker, side_norm)
        except Exception as e:
            logger.exception("get_levels: MarketData.get_entry_price raised: %s", e)
            entry_price = None

        if entry_price is None:
            logger.debug("get_levels: no entry price for %s", ticker)
            return None, None

        if side_norm in ("buy", "long"):
            return FixedSLTP.long_levels(entry_price)
        elif side_norm in ("sell", "short"):
            return FixedSLTP.short_levels(entry_price)
        else:
            logger.debug("get_levels: unknown side '%s' for ticker %s", side, ticker)
            return None, None
