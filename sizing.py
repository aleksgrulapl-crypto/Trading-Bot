# ============================
# POSITION SIZING MODULE (Module 4)
# ============================

from session import auth
from market import MarketData
from config import (
    API_POSITIONS,
    API_ACCOUNTS,
    EQUITY_PERCENT,
    MAX_POSITIONS_PER_TICKER
)

class PositionSizing:

    @staticmethod
    def get_equity():
        r = auth.request("GET", API_ACCOUNTS)

        if r.status_code != 200:
            raise Exception(f"Equity fetch failed: {r.text}")

        data = r.json()
        account = data["accounts"][0]

        # Your confirmed format:
        # account["balance"]["available"]
        if "balance" in account and "available" in account["balance"]:
            return float(account["balance"]["available"])

        # Fallbacks
        for key in ["available", "availableCash", "availableFunds", "cash"]:
            if key in account:
                return float(account[key])

        print("ACCOUNT JSON (unknown format):", account)
        raise Exception("Could not find usable equity field in account JSON")

    @staticmethod
    def count_positions(ticker):
        r = auth.request("GET", API_POSITIONS)

        if r.status_code != 200:
            raise Exception(f"Position count failed: {r.text}")

        positions = r.json().get("positions", [])

        count = 0
        for p in positions:
            epic = (
                p.get("market", {}).get("epic")
                or p.get("market", {}).get("instrumentName")
            )
            if epic and epic.upper() == ticker.upper():
                count += 1

        return count

    @staticmethod
    def calculate_size(ticker, side):
        if PositionSizing.count_positions(ticker) >= MAX_POSITIONS_PER_TICKER:
            print(f"Max positions reached for {ticker}. Skipping trade.")
            return None

        equity = PositionSizing.get_equity()
        allocation = equity * EQUITY_PERCENT

        entry_price = MarketData.get_entry_price(ticker, side)
        raw_size = allocation / entry_price

        final_size = MarketData.validate_size(ticker, raw_size)
        return round(final_size, 2)
