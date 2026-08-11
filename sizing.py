# ============================
# POSITION SIZING MODULE (Updated - No MarketData dependency)
# ============================

from session import auth, request
from config import (
    API_POSITIONS,
    API_ACCOUNTS,
    API_MARKET,
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

        if "balance" in account and "available" in account["balance"]:
            return float(account["balance"]["available"])

        for key in ["available", "availableCash", "availableFunds", "cash"]:
            if key in account:
                return float(account[key])

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
    def get_entry_price(epic, side):
        r = auth.request("GET", f"{API_MARKET}/{epic}")

        if r.status_code != 200:
            raise Exception(f"Market price fetch failed: {r.text}")

        snapshot = r.json().get("snapshot", {})

        if side.lower() == "buy":
            return float(snapshot.get("offer"))
        else:
            return float(snapshot.get("bid"))

    @staticmethod
    def validate_size(size):
        # Basic validation (can be expanded)
        if size < 0.1:
            return 0.1
        return size

    @staticmethod
    def calculate_size(ticker, side):
        if PositionSizing.count_positions(ticker) >= MAX_POSITIONS_PER_TICKER:
            print(f"Max positions reached for {ticker}. Skipping trade.")
            return None

        equity = PositionSizing.get_equity()
        allocation = equity * EQUITY_PERCENT

        # Get EPIC from session
        epic_data = request("GET", f"{API_MARKET}/search/{ticker}").json()
        epic = epic_data.get("epic", ticker)

        entry_price = PositionSizing.get_entry_price(epic, side)
        raw_size = allocation / entry_price

        final_size = PositionSizing.validate_size(raw_size)
        return round(final_size, 2)
