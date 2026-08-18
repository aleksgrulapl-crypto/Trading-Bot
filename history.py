# ============================
# HISTORY MODULE (AUTO-IMPORT CAPITAL CLOSED TRADES)
# ============================

import session
from trade_log import save_log, load_raw_log
from datetime import datetime
from config import API_HISTORY_TRANSACTIONS


# ---------------------------------------------------------
# TIMESTAMP PARSING
# ---------------------------------------------------------

def _parse_capital_timestamp(value):
    """
    Capital.com returns timestamps in multiple formats:
    - Unix seconds
    - Unix milliseconds
    - ISO strings with/without Z
    This function normalizes all of them.
    """
    if value is None:
        return None

    # Numeric timestamps
    if isinstance(value, (int, float)):
        try:
            # Milliseconds
            if value > 1e12:
                dt = datetime.utcfromtimestamp(value / 1000.0)
            else:
                dt = datetime.utcfromtimestamp(value)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    # ISO strings
    if isinstance(value, str):
        try:
            cleaned = value.replace("Z", "")
            dt = datetime.fromisoformat(cleaned)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    return None


# ---------------------------------------------------------
# FETCH CLOSED TRADES FROM CAPITAL.COM
# ---------------------------------------------------------

def fetch_closed_trades():
    """
    Fetch closed trades from Capital.com history endpoint.
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
# CONVERT CAPITAL TRADE → INTERNAL FORMAT
# ---------------------------------------------------------

def convert_capital_trade(raw):
    """
    Convert Capital.com closed trade format into our internal CLOSED trade event.
    Fully compatible with merge_trades() and dashboard.
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
        instrument_name = raw.get("instrumentName")

        sl = raw.get("stopLevel")
        tp = raw.get("limitLevel")

        open_ts_raw = raw.get("date")
        close_ts_raw = raw.get("closeDate")

        open_timestamp = _parse_capital_timestamp(open_ts_raw)
        close_timestamp = _parse_capital_timestamp(close_ts_raw)

        entry = {
            "time": close_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": instrument_name or epic,
            "epic": epic,
            "dealId": deal_id,
            "side": (direction or "CLOSE").upper(),
            "size": float(size) if size is not None else 0.0,
            "entry_price": float(entry_price) if entry_price is not None else None,
            "exit_price": float(exit_price) if exit_price is not None else None,
            "pnl": float(pnl) if pnl is not None else 0.0,
            "sl": sl,
            "tp": tp,
            "trail": None,
            "timeframe": None,

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

            "cumulative_pnl": None,
            "running_peak": None,
            "drawdown": None
        }

        return entry

    except Exception as e:
        print(f"[HISTORY] Conversion error: {e}")
        return None


# ---------------------------------------------------------
# MERGE HISTORY INTO trade_log.json
# ---------------------------------------------------------

def merge_history():
    """
    Import CLOSED trades from Capital.com into our persistent trade log.
    Only adds CLOSED trades that do not already exist.
    """
    print("[HISTORY] Fetching closed trades...")
    closed_trades = fetch_closed_trades()

    if not closed_trades:
        print("[HISTORY] No closed trades found.")
        return

    log = load_raw_log()

    # Only skip if CLOSED already exists for that dealId
    existing_closed = {e.get("dealId") for e in log if e.get("status") == "CLOSED"}

    added = 0

    for raw in closed_trades:
        deal_id = raw.get("dealId")

        if deal_id in existing_closed:
            continue

        entry = convert_capital_trade(raw)
        if entry:
            log.append(entry)
            added += 1

    save_log(log)

    print(f"[HISTORY] Added {added} new closed trades from Capital.com.")


# ---------------------------------------------------------
# FETCH SINGLE CLOSED TRADE BY DEAL ID
# ---------------------------------------------------------

def get_closed_trade_by_deal(deal_id):
    """
    Fetch a single closed trade from Capital.com history.
    """
    trades = fetch_closed_trades()
    for raw in trades:
        if raw.get("dealId") == deal_id:
            return convert_capital_trade(raw)
    return None