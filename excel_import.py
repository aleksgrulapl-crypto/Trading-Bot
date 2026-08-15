import pandas as pd

def load_excel_trades(path="Trading Log 2026.xlsx"):
    try:
        df = pd.read_excel(path, sheet_name="Trade Log")
    except Exception:
        return []

    trades = []
    for _, row in df.iterrows():
        trades.append({
            "trade_id": row.get("Trade ID"),
            "ticker": row.get("Ticker"),
            "side": row.get("Direction"),
            "size": row.get("Position Size"),
            "entry_price": row.get("Entry Price"),
            "exit_price": row.get("Exit Price"),
            "pnl": row.get("Outcome (P/L)"),
            "sl": row.get("SL"),
            "tp": row.get("TP"),
            "open_timestamp": row.get("Open Timestamp (UTC)"),
            "close_timestamp": row.get("Close Timestamp (UTC)"),
            "notes": row.get("Notes"),
            "checklist_passed": row.get("Checklist Passed?"),
        })
    return trades
