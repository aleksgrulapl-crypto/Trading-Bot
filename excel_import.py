import pandas as pd
from trade_log import load_raw_log, save_log


def load_excel_trades(path="Trading Log 2026.xlsx"):
    """
    Load trades from Excel 'Trade Log' sheet using the correct header row (row 2).
    """
    try:
        df = pd.read_excel(path, sheet_name="Trade Log", header=2)
    except Exception:
        return []

    trades = []

    for _, row in df.iterrows():
        ticker = row.get("Ticker")
        status = row.get("Status")

        # Skip non-trade rows
        if pd.isna(ticker) or pd.isna(status):
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

        entry = {
            "time": row.get("Close Timestamp (UTC)") or row.get("Open Timestamp (UTC)"),
            "open_timestamp": row.get("Open Timestamp (UTC)"),
            "close_timestamp": row.get("Close Timestamp (UTC)"),

            "ticker": ticker,
            "epic": ticker,
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
            "status": status,
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
