# ============================
# MARKET DATA MODULE (Module 3)
# ============================

from session import auth
from config import API_MARKET, TICKER_SETTINGS

class MarketData:

    @staticmethod
    def get_snapshot(ticker):
        url = f"{API_MARKET}/{ticker}"
        r = auth.request("GET", url)

        if r.status_code != 200:
            raise Exception(f"Market snapshot failed for {ticker}: {r.text}")

        data = r.json()

        snapshot = data.get("snapshot", {})
        dealing = data.get("dealingRules", {})
        min_size = dealing.get("minDealSize", {}).get("value", 0.1)

        return {
            "bid": float(snapshot.get("bid", 0)),
            "offer": float(snapshot.get("offer", 0)),
            "market_status": snapshot.get("marketStatus", "UNKNOWN"),
            "min_size": float(min_size)
        }

    @staticmethod
    def get_entry_price(ticker, side):
        snap = MarketData.get_snapshot(ticker)
        return snap["offer"] if side == "buy" else snap["bid"]

    @staticmethod
    def validate_size(ticker, size):
        config_min = TICKER_SETTINGS.get(ticker, {}).get("min_size")
        market_min = MarketData.get_snapshot(ticker)["min_size"]

        if config_min is None:
            return max(size, market_min)

        return max(size, config_min)
