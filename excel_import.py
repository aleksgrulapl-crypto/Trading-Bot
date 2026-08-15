import pandas as pd


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
        pnl_raw = row.get("Outcome (P/L)")
        pnl = 0.0
        if isinstance(pnl_raw, str):
            cleaned = (
                pnl_raw.replace("£", "")
                       .replace(",", "")
                       .strip()
            )
            try:
                pnl = float(cleaned)
            except ValueError:
                pnl = 0.0
        else:
            try:
                pnl = float(pnl_raw)
            except (TypeError, ValueError):
                pnl = 0.0

        direction = row.get("Direction")
        side = direction.upper() if isinstance(direction, str) else direction

        trades.append({
            "time": row.get("Open Timestamp (UTC)"),
            "open_timestamp": row.get("Open Timestamp (UTC)"),
            "close_timestamp": row.get("Close Timestamp (UTC)"),
            "ticker": row.get("Ticker"),
            "side": side,
            "size": row.get("Position Size"),
            "entry_price": row.get("Entry Price"),
            "exit_price": row.get("Exit Price"),
            "pnl": pnl,
            "checklist_passed": row.get("Checklist Passed?"),
            "notes": row.get("Notes"),

            "trade_id": row.get("Trade ID"),
            "currency": row.get("Currency"),
            "platform": row.get("Trading Platform"),
            "sl": row.get("SL"),
            "tp": row.get("TP"),
            "reason": row.get("Reason for Entry"),
            "close_source": row.get("Close Source"),
            "status": row.get("Status"),
            "fees": row.get("Fees / Adjustments"),
            "cumulative_pnl": row.get("Cumulative P/L"),
            "running_peak": row.get("Running Peak"),
            "drawdown": row.get("Drawdown"),
        })
    return trades
