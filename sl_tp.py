# ============================
# FIXED SL/TP MODULE (Module 5 — Corrected + Stable)
# ============================

from market import MarketData
from config import FIXED_SL_PERC, FIXED_TP_PERC


class FixedSLTP:
    """
    Module 5 — Fixed SL/TP logic.
    Backend version (clean + stable).
    """

    @staticmethod
    def long_levels(entry_price):
        """
        Long trade:
        SL = entry * (1 - FIXED_SL_PERC)
        TP = entry * (1 + FIXED_TP_PERC)
        """
        sl = entry_price * (1 - FIXED_SL_PERC)
        tp = entry_price * (1 + FIXED_TP_PERC)
        return round(sl, 2), round(tp, 2)

    @staticmethod
    def short_levels(entry_price):
        """
        Short trade:
        SL = entry * (1 + FIXED_SL_PERC)
        TP = entry * (1 - FIXED_TP_PERC)
        """
        sl = entry_price * (1 + FIXED_SL_PERC)
        tp = entry_price * (1 - FIXED_TP_PERC)
        return round(sl, 2), round(tp, 2)

    @staticmethod
    def get_levels(ticker, side):
        """
        Main wrapper:
        - Fetches entry price from MarketData
        - Returns correct SL/TP pair
        """
        entry_price = MarketData.get_entry_price(ticker, side)

        if side.lower() == "buy":
            return FixedSLTP.long_levels(entry_price)

        return FixedSLTP.short_levels(entry_price)