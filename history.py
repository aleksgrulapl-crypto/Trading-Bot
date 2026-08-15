# ============================
# HISTORY MODULE (AUTO-IMPORT CAPITAL CLOSED TRADES)
# ============================

import session
import trade_log
from datetime import datetime
from config import API_HISTORY_TRANSACTIONS


# ---------------------------------------------------------
# FETCH CLOSED TRADES FROM CAPITAL.COM
# ---------------------------------------------------------

def fetch_closed_trades():
    """
    Pulls closed trades from Capital.com using the official history endpoint.
    Returns a list of raw Capital.com trade objects.
    """

    url = f"{API_HISTORY_TRANSACTIONS}?type=POSITION"
    response = session.request("GET", url)

    if not response or response.status_code != 200:
        print("[HISTORY] Failed to fetch closed trades")
        return []

    try:
        data = response.json()
        return data.get("transactions", [])
    except Exception as e:
        print(f"[HISTORY] JSON parse error: {e}")
        return []


# ---------------------------------------------------------
# CONVERT CAPITAL TRADE → INTERNAL TRADE LOG FORMAT
# ---------------------------------------------------------

def convert_capital_trade(raw):
    """
    Converts a single Capital.com trade into your upgraded trade_log.py format.
    """

    try:
        deal_id = raw.get("dealId")
        epic = raw.get("epic")
        direction = raw.get("direction")
        size = raw.get("size")
        entry_price = raw.get("level")
        exit_price = raw.get("closeLevel")
        pnl = raw.get("profitLoss")
        currency = raw.get("currency")
        open_ts = raw.get("date")
        close_ts = raw.get("closeDate")

        # Convert timestamps
        try:
            open_timestamp = datetime.utcfromtimestamp(open_ts).strftime("%Y-%m-%d %H:%M:%S")
        except:
            open_timestamp = None

        try:
            close_timestamp = datetime.utcfromtimestamp(close_ts).strftime("%Y-%m-%d %H:%M:%S")
        except:
            close_timestamp = None

        # Build upgraded entry
        entry = {
            # Existing fields (backend compatibility)
            "time": close_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": raw.get("instrumentName"),
            "epic": epic,
            "dealId": deal_id,
            "side": direction.upper(),
            "size": float(size),
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "pnl": float(pnl),
            "sl": raw.get("stopLevel"),
            "tp": raw.get("limitLevel"),
            "trail": None,
            "timeframe": None,

            # New fields (Excel Trade Log compatibility)
            "trade_id": deal_id,
            "open_timestamp": open_timestamp,
            "close_timestamp": close_timestamp,
            "currency": currency or "USD",
            "platform": "Capital",
            "reason": None,
            "checklist_passed": None,
            "close_source": "CAPITAL_HISTORY",
            "status": "CLOSED",
            "notes": None,
            "fees": raw.get("charges", 0.0),

            # Performance fields (filled later)
            "cumulative_pnl": None,
            "running_peak": None,
            "drawdown": None
        }

        return entry

    except Exception as e:
        print(f"[HISTORY] Conversion error: {e}")
        return None


# ---------------------------------------------------------
# MERGE INTO trade_log.json SAFELY
# ---------------------------------------------------------

def merge_history():
    """
    Fetches closed trades from Capital.com and merges them into trade_log.json.
    Avoids duplicates using dealId.
    """

    print("[HISTORY] Fetching closed trades...")
    closed_trades = fetch_closed_trades()

    if not closed_trades:
        print("[HISTORY] No closed trades found.")
        return

    log = trade_log.load_log()
    existing_ids = {entry.get("dealId") for entry in log}

    added = 0

    for raw in closed_trades:
        deal_id = raw.get("dealId")

        # Skip duplicates
        if deal_id in existing_ids:
            continue

        entry = convert_capital_trade(raw)
        if entry:
            log.append(entry)
            added += 1

    trade_log.save_log(log)

    print(f"[HISTORY] Added {added} new closed trades from Capital.com.")
