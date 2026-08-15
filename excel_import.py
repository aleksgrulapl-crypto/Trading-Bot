import pandas as pd
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


def load_excel_trades(path="Trading Log 2026.xlsx"):
    """
    Load trades from Excel 'Trade Log' sheet using manual column mapping.
    Skip the first row because it contains header labels.
    """
    try:
        df = pd.read_excel(path, sheet_name="Trade Log", header=2)
    except Exception:
        return []

    trades = []

    # Skip row 0 (header row)
    df = df.iloc[1:]

    for _, row in df.iterrows():
        ticker = row.get(COLUMN_MAP["ticker"])
        status = row.get(COLUMN_MAP["status"])

        # Skip non-trade rows
        if pd.isna(ticker) or pd.isna(status):
            continue

        # PNL
        pnl_raw = row.get(COLUMN_MAP["pnl"])
        try:
            pnl = float(str(pnl_raw).replace("£", "").replace(",", "").strip())
        except Exception:
            pnl = 0.0

        # Side
        direction = row.get(COLUMN_MAP["direction"])
        side = direction.upper() if isinstance(direction, str) else direction

        entry = {
            "time": row.get(COLUMN_MAP["close_ts"]) or row.get(COLUMN_MAP["open_ts"]),
            "open_timestamp": row.get(COLUMN_MAP["open_ts"]),
            "close_timestamp": row.get(COLUMN_MAP["close_ts"]),

            "ticker": ticker,
            "epic": ticker,
            "side": side,
            "size": float(row.get(COLUMN_MAP["size"]) or 0),

            "entry_price": float(row.get(COLUMN_MAP["entry_price"]) or 0),
            "exit_price": float(row.get(COLUMN_MAP["exit_price"]) or 0),
            "pnl": pnl,

            "sl": row.get(COLUMN_MAP["sl"]),
            "tp": row.get(COLUMN_MAP["tp"]),

            "reason": row.get(COLUMN_MAP["reason"]),
            "checklist_passed": row.get(COLUMN_MAP["checklist"]),
            "close_source": row.get(COLUMN_MAP["close_source"]),
            "status": status,
            "notes": row.get(COLUMN_MAP["notes"]),
            "fees": float(row.get(COLUMN_MAP["fees"]) or 0),

            "trade_id": row.get(COLUMN_MAP["trade_id"]),
            "currency": row.get(COLUMN_MAP["currency"]),
            "platform": row.get(COLUMN_MAP["platform"]),

            "cumulative_pnl": row.get(COLUMN_MAP["cumulative_pnl"]),
            "running_peak": row.get(COLUMN_MAP["running_peak"]),
            "drawdown": row.get(COLUMN_MAP["drawdown"]),

            "trail": None,
            "timeframe": None,
        }

        trades.append(entry)

    return trades


def import_excel_into_log(path="Trading Log 2026.xlsx"):
    """
    Merge Excel trades into trade_log.json safely.
    Avoid duplicates using trade_id.
    """
    excel_trades = load_excel_trades(path)
    if not excel_trades:
        print("[EXCEL IMPORT] No valid trades found.")
        return

    log = load_raw_log()
    existing_ids = {t.get("trade_id") for t in log}

    added = 0
    for t in excel_trades:
        tid = t.get("trade_id")
        if tid in existing_ids:
            continue
        log.append(t)
        added += 1

    save_log(log)
    print(f"[EXCEL IMPORT] Imported {added} Excel trades.")
