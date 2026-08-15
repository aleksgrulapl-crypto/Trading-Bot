import pandas as pd

def load_excel_trades(path="Trading Log 2026.xlsx"):
    try:
        df = pd.read_excel(path, sheet_name="Trade Log")
    except Exception:
        return []

    trades = []
    for _, row in df.iterrows():
        # PnL: strip currency symbol and commas, convert to float
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

        trades.append({
            "trade_id": row.get("Trade ID"),
            "open_timestamp": row.get("Open Timestamp (UTC)"),
            "close_timestamp": row.get("Close Timestamp (UTC)"),
            "ticker": row.get("Ticker"),
            "currency": row.get("Currency"),
            "side": row.get("Direction"),
            "platform": row.get("Trading Platform"),
            "entry_price": row.get("Entry Price"),
            "exit_price": row.get("Exit Price"),
            "sl": row.get("SL"),
            "tp": row.get("TP"),
            "size": row.get("Position Size"),
            "reason": row.get("Reason for Entry"),
            "checklist_passed": row.get("Checklist Passed?"),
            "close_source": row.get("Close Source"),
            "status": row.get("Status"),
            "notes": row.get("Notes"),
            "fees": row.get("Fees / Adjustments"),
            "pnl": pnl,
            "cumulative_pl": row.get("Cumulative P/L"),
            "running_peak": row.get("Running Peak"),
            "drawdown": row.get("Drawdown"),
        })
    return trades
