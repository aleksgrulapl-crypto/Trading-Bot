import pandas as pd
from datetime import datetime
from trade_log import load_raw_log, save_log


def _convert_timestamp(value):
    """
    Convert Excel serial timestamps or return strings unchanged.
    """
    if value is None:
        return None

    if isinstance(value, str):
        return value

    try:
        base = datetime(1899, 12, 30)
        dt = base + pd.to_timedelta(value, unit="D")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _is_valid_row(row):
    """
    Reject empty or header-like rows.
    """
    if not isinstance(row.get("Ticker"), str):
        return False
    if row.get("Status") != "CLOSED":
        return False
    if row.get("Entry Price") is None or row.get("Exit Price") is None:
        return False
    return True


def load_excel_trades(path="Trading Log 2026.xlsx"):
    """
    Load trades from Excel 'Trade Log' sheet into unified trade dicts.
    """
    try:
        df = pd.read_excel(path, sheet_name="Trade Log")
    except Exception:
        return []

    trades = []

    for _, row in df.iterrows():
        if not _is_valid_row(row):
            continue

        # PNL
        pnl_raw = row.get("Outcome (P/L)")
        try:
            pnl = float(str(pnl_raw).replace("£", "").replace(",", "").strip())
        except Exception:
            pnl = 0.0

        # Side
        direction = row.get("Direction")
        side = direction.upper() if isinstance(direction, str) else direction

        # Timestamps
        open_ts = _convert_timestamp(row.get("Open Timestamp (UTC)"))
        close_ts = _convert_timestamp(row.get("Close Timestamp (UTC)"))

        entry = {
            "time": close_ts or open_ts,
            "open_timestamp": open_ts,
            "close_timestamp": close_ts,

            "ticker": row.get("Ticker"),
            "epic": row.get("Ticker"),
            "side": side,
            "size": float(row.get("Position Size") or 0),

            "entry_price": float(row.get("Entry Price") or 0),
            "exit_price": float(row.get("Exit Price") or 0),
            "pnl": pnl,

            "sl": row.get("SL"),
            "tp": row.get("TP"),

            "reason": row.get("Reason for Entry"),
            "checklist_passed": row.get("Checklist Passed?"),
            "close_source": row.get("Close Source"),
            "status": "CLOSED",
            "notes": row.get("Notes"),
            "fees": float(row.get("Fees / Adjustments") or 0),

            "trade_id": row.get("Trade ID"),
            "currency": row.get("Currency"),
            "platform": row.get("Trading Platform"),

            "cumulative_pnl": row.get("Cumulative P/L"),
            "running_peak": row.get("Running Peak"),
            "drawdown": row.get("Drawdown"),

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
