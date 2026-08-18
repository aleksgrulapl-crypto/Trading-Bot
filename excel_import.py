# ============================
# EXCEL IMPORT MODULE (UNIFIED FORMAT + SYNTHETIC DEALIDS)
# ============================

import pandas as pd
import math
from trade_log import load_raw_log, save_log

COLUMN_MAP = {
    "trade_id": "Unnamed: 0",
    "open_ts": "Unnamed: 1",
    "close_ts": "Unnamed: 2",
    "ticker": "Unnamed: 3",
    "currency": "Unnamed: 4",
    "direction": "Unnamed: 5",
    "platform": "Unnamed: 6",
    "entry_price": "Unnamed: 7",
    "exit_price": "Unnamed: 8",
    "sl": "Unnamed: 9",
    "tp": "Unnamed: 10",
    "size": "Unnamed: 11",
    "reason": "Unnamed: 12",
    "checklist": "Unnamed: 13",
    "close_source": "Unnamed: 14",
    "status": "Unnamed: 15",
    "notes": "Unnamed: 16",
    "fees": "Unnamed: 17",
    "pnl": "Unnamed: 18",
    "cumulative_pnl": "Unnamed: 19",
    "running_peak": "Unnamed: 20",
    "drawdown": "Unnamed: 21",
}


def _clean(value):
    """Convert NaN → None, datetime → string."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def load_excel_trades(path="Trading Log 2026.xlsx"):
    """
    Load trades from Excel and convert them into the unified live trade format.
    """
    try:
        df = pd.read_excel(path, sheet_name="Trade Log", header=2)
    except Exception:
        return []

    df = df.iloc[1:]  # skip header row inside sheet

    trades = []

    for _, row in df.iterrows():
        ticker = row.get(COLUMN_MAP["ticker"])
        status_raw = row.get(COLUMN_MAP["status"])

        if pd.isna(ticker) or pd.isna(status_raw):
            continue

        # Normalize status
        status = str(status_raw).strip().upper()
        if status not in ("OPEN", "CLOSED"):
            status = "CLOSED" if row.get(COLUMN_MAP["exit_price"]) else "OPEN"

        # Normalize direction
        direction_raw = row.get(COLUMN_MAP["direction"])
        side = str(direction_raw).upper() if isinstance(direction_raw, str) else None

        # PNL
        pnl_raw = row.get(COLUMN_MAP["pnl"])
        try:
            pnl = float(str(pnl_raw).replace("£", "").replace(",", "").strip())
        except Exception:
            pnl = None

        # Unified format entry
        entry = {
            "dealId": None,  # synthetic later
            "ticker": _clean(ticker),
            "side": side,
            "size": float(row.get(COLUMN_MAP["size"]) or 0),

            "time_entered": _clean(row.get(COLUMN_MAP["open_ts"])),
            "time_exited": _clean(row.get(COLUMN_MAP["close_ts"])),

            "entry_price": float(row.get(COLUMN_MAP["entry_price"]) or 0),
            "exit_price": float(row.get(COLUMN_MAP["exit_price"]) or 0),

            "pnl": pnl,
            "status": status,

            # Optional fields preserved
            "sl": _clean(row.get(COLUMN_MAP["sl"])),
            "tp": _clean(row.get(COLUMN_MAP["tp"])),
            "timeframe": None,
        }

        trades.append(entry)

    return trades


def import_excel_into_log(path="Trading Log 2026.xlsx"):
    excel_trades = load_excel_trades(path)
    if not excel_trades:
        print("[EXCEL IMPORT] No valid trades found.")
        return

    log = load_raw_log()

    # Assign synthetic dealIds
    existing_ids = {t.get("dealId") for t in log}
    counter = 1

    added = 0
    for t in excel_trades:
        # Generate synthetic dealId
        synthetic_id = f"excel_{counter:04d}"
        counter += 1

        # Ensure no collision
        while synthetic_id in existing_ids:
            synthetic_id = f"excel_{counter:04d}"
            counter += 1

        t["dealId"] = synthetic_id
        existing_ids.add(synthetic_id)

        log.append(t)
        added += 1

    save_log(log)
    print(f"[EXCEL IMPORT] Imported {added} Excel trades (unified format).")
