# ============================
# FIXED SL/TP MODULE (Module 5)
# ============================

from market import MarketData
from config import FIXED_SL_PERC, FIXED_TP_PERC

class FixedSLTP:

    @staticmethod
    def long_levels(entry_price):
        sl = entry_price * (1 - FIXED_SL_PERC)
        tp = entry_price * (1 + FIXED_TP_PERC)
        return round(sl, 2), round(tp, 2)

    @staticmethod
    def short_levels(entry_price):
        sl = entry_price * (1 + FIXED_SL_PERC)
        tp = entry_price * (1 - FIXED_TP_PERC)
        return round(sl, 2), round(tp, 2)

    @staticmethod
    def get_levels(ticker, side):
        entry_price = MarketData.get_entry_price(ticker, side)
        return (
            FixedSLTP.long_levels(entry_price)
            if side == "buy"
            else FixedSLTP.short_levels(entry_price)
        )
