# ============================
# SYNC CLOSED TRADES (AUTO-DETECT SL/TP/MANUAL CLOSES)
# ============================

import session
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_HISTORY_TRANSACTIONS


def sync_closed_trades():
    """
    Detect CLOSED trades by comparing OPEN trades in trade_log.json
    with Capital.com history/transactions.

    This restores:
    - SL/TP hit detection
    - manual close detection
    - exit price logging
    - realized PnL logging
    - analytics updates
    """

    # 1. Load all trades
    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    # 2. Fetch history from Capital.com
    r = session.request("GET", f"{API_HISTORY_TRANSACTIONS}?max=200")
    if not r or r.status_code != 200:
        print("[SYNC] Failed to fetch history")
        return

    data = r.json()
    transactions = data.get("transactions", [])

    # 3. Index history by dealId / positionId
    history_map = {}
    for tx in transactions:
        tx_id = tx.get("dealId") or tx.get("positionId")
        if tx_id:
            history_map[str(tx_id)] = tx

    # 4. Check each OPEN trade
    for trade in open_trades:
        deal_id = str(trade.get("dealId"))
        if not deal_id:
            continue

        # If this dealId exists in history → it's CLOSED
        tx = history_map.get(deal_id)
        if not tx:
            continue

        # Extract exit price + realized PnL
        exit_price = (
            tx.get("closeLevel")
            or tx.get("level")
            or tx.get("price")
        )

        pnl = (
            tx.get("profitAndLoss")
            or tx.get("pnl")
        )

        # Extract exit timestamp
        time_exited = tx.get("date") or timestamp()

        print(f"[SYNC] CLOSED detected → dealId={deal_id}, exit={exit_price}, pnl={pnl}")

        # Log CLOSED trade
        log_closed_trade(
            ticker=trade.get("ticker"),
            epic=trade.get("epic"),
            deal_id=deal_id,
            side=trade.get("side"),
            size=trade.get("size"),
            entry_price=trade.get("entry_price"),
            exit_price=exit_price,
            pnl=pnl,
            sl=trade.get("sl"),
            tp=trade.get("tp"),
            timeframe=trade.get("timeframe"),
            time_entered=trade.get("time_entered"),
            timestamp=time_exited
        )
