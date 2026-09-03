# fixed_sltp.py
# Fixed SL/TP logic with minimum SL distance enforcement

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
    Uses FIXED_SL_PERC and FIXED_TP_PERC from config.
    Enforces a minimum SL distance (MIN_SL_PERC) to avoid overly tight stops.
    """

    @staticmethod
    def _get_percents() -> Tuple[float, float, float]:
        sl_perc = float(getattr(config, "FIXED_SL_PERC", 0.20))  # default 20%
        tp_perc = float(getattr(config, "FIXED_TP_PERC", 0.40))  # default 40%
        min_sl_perc = float(getattr(config, "MIN_SL_PERC", 0.005))  # minimum 0.5% distance
        # If user complained SLs were too small, allow a safety multiplier
        safety_mult = float(getattr(config, "FIXED_SL_SAFETY_MULT", 1.0))
        sl_perc = max(sl_perc * safety_mult, min_sl_perc)
        return sl_perc, tp_perc, min_sl_perc

    @staticmethod
    def long_levels(entry_price: float) -> Tuple[Optional[float], Optional[float]]:
        try:
            if entry_price is None:
                return None, None
            sl_perc, tp_perc, min_sl = FixedSLTP._get_percents()
            # SL distance (absolute)
            sl = float(entry_price) * (1.0 - sl_perc)
            tp = float(entry_price) * (1.0 + tp_perc)
            # enforce minimum absolute distance if configured (min_sl is a percentage)
            min_distance = float(entry_price) * min_sl
            if (entry_price - sl) < min_distance:
                sl = entry_price - min_distance
            return round(sl, 4), round(tp, 4)
        except Exception as e:
            logger.exception("long_levels error: %s", e)
            return None, None

    @staticmethod
    def short_levels(entry_price: float) -> Tuple[Optional[float], Optional[float]]:
        try:
            if entry_price is None:
                return None, None
            sl_perc, tp_perc, min_sl = FixedSLTP._get_percents()
            sl = float(entry_price) * (1.0 + sl_perc)
            tp = float(entry_price) * (1.0 - tp_perc)
            min_distance = float(entry_price) * min_sl
            if (sl - entry_price) < min_distance:
                sl = entry_price + min_distance
            return round(sl, 4), round(tp, 4)
        except Exception as e:
            logger.exception("short_levels error: %s", e)
            return None, None

    @staticmethod
    def get_levels(ticker: str, side: str) -> Tuple[Optional[float], Optional[float]]:
        if not ticker or not side:
            logger.debug("get_levels: missing ticker or side")
            return None, None
        side_norm = str(side).strip().lower()
        try:
            entry_price = MarketData.get_entry_price(ticker, side_norm)
        except Exception as e:
            logger.exception("get_levels: MarketData.get_entry_price raised: %s", e)
            entry_price = None

        if entry_price is None:
            return None, None

        if side_norm in ("buy", "long"):
            return FixedSLTP.long_levels(entry_price)
        elif side_norm in ("sell", "short"):
            return FixedSLTP.short_levels(entry_price)
        else:
            logger.debug("get_levels: unknown side '%s' for ticker %s", side, ticker)
            return None, None
