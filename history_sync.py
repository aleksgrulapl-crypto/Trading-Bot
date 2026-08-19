def sync_closed_trades():
    """
    Detect CLOSED trades by matching OPEN trades with history transactions
    using epic + size + entry_price instead of dealId (Capital.com is inconsistent).
    """

    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    # Fetch history
    r = session.request("GET", f"{API_HISTORY_TRANSACTIONS}?max=200")
    if not r or r.status_code != 200:
        print("[SYNC] Failed to fetch history")
        return

    data = r.json()
    transactions = data.get("transactions", [])

    # Loop through OPEN trades
    for trade in open_trades:
        epic = trade.get("epic")
        size = float(trade.get("size"))
        entry_price = float(trade.get("entry_price"))
        direction = trade.get("side")

        # Try to find a matching CLOSED transaction
        for tx in transactions:

            # Must match same market
            if tx.get("market") != epic:
                continue

            # Must match same direction
            if tx.get("direction", "").upper() != direction.upper():
                continue

            # Must match size (Capital.com uses float)
            tx_size = float(tx.get("size", 0))
            if abs(tx_size - size) > 0.0001:
                continue

            # Must match entry price (within tolerance)
            tx_entry = tx.get("level") or tx.get("openLevel")
            if tx_entry is None:
                continue

            if abs(float(tx_entry) - entry_price) > 0.05:
                continue

            # If we reach here → CLOSED trade found
            exit_price = tx.get("closeLevel") or tx.get("level")
            pnl = tx.get("profitAndLoss") or tx.get("pnl")
            time_exited = tx.get("date") or timestamp()

            print(f"[SYNC] CLOSED detected → epic={epic}, exit={exit_price}, pnl={pnl}")

            log_closed_trade(
                ticker=trade.get("ticker"),
                epic=epic,
                deal_id=trade.get("dealId"),
                side=direction,
                size=size,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                sl=trade.get("sl"),
                tp=trade.get("tp"),
                timeframe=trade.get("timeframe"),
                time_entered=trade.get("time_entered"),
                timestamp=time_exited
            )

            break  # stop scanning transactions for this trade
