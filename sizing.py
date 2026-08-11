# ============================
# POSITION SIZING MODULE (FINAL)
# ============================

from session import auth
from config import (
    API_POSITIONS,
    API_ACCOUNTS,
    API_MARKET,
    EQUITY_PERCENT,
    LEVERAGE,
    MAX_POSITIONS_PER_TICKER
)

class PositionSizing:

    @staticmethod
    def get_available_balance():
        r = auth.request("GET", API_ACCOUNTS)

        if r.status_code != 200:
            raise Exception(f"Account fetch failed: {r.text}")

        data = r.json()
        account = data["accounts"][0]

        # IG format: account["balance"]["available"]
        if "balance" in account and "available" in account["balance"]:
            return float(account["balance"]["available"])

        raise Exception("Available balance field missing in account JSON")

    @staticmethod
    def count_positions(epic):
        r = auth.request("GET", API_POSITIONS)

        if r.status_code != 200:
            raise Exception(f"Position count failed: {r.text}")

        positions = r.json().get("positions", [])

        count = 0
        for p in positions:
            pos_epic = p.get("market", {}).get("epic")
            if pos_epic and pos_epic.upper() == epic.upper():
                count += 1

        return count

    @staticmethod
    def get_entry_price(epic, side):
        r = auth.request("GET", f"{API_MARKET}/{epic}")

        snapshot = r.json().get("snapshot", {})

        if side.lower() == "buy":
            return float(snapshot.get("offer"))
        else:
            return float(snapshot.get("bid"))

    @staticmethod
    def calculate_size(epic, side):
        if PositionSizing.count_positions(epic) >= MAX_POSITIONS_PER_TICKER:
            print(f"Max positions reached for {epic}. Skipping trade.")
            return None

        available = PositionSizing.get_available_balance()
        allocation = available * EQUITY_PERCENT

        entry_price = PositionSizing.get_entry_price(epic, side)

        raw_size = (allocation * LEVERAGE) / entry_price

        return round(raw_size, 2)
