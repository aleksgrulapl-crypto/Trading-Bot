# ============================
# SYNC CLOSED TRADES (DISAPPEARANCE + MARKET SNAPSHOT)
# ============================

import session
from trade_log import load_raw_log, log_closed_trade   # ⭐ REQUIRED IMPORT
from utils import timestamp
from config import API_MARKET


def sync_closed_trades():
    """
    Detect CLOSED trades by checking which OPEN trades disappeared from /positions.
    Compute exit price using live market snapshot (bid/offer).
    Compute PnL manually.
    """

    # 1. Load trade log
    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    # 2. Fetch current positions
    raw_positions = session.get_positions() or []
    enriched_positions = session.enrich_positions(raw_positions) or []

    # Build a set of currently open dealIds
    live_ids = set(str(p.get("id")) for p in enriched_positions)

    # 3. Check each OPEN trade
    for trade in open_trades:
        deal_id = str(trade.get("dealId"))

        # If dealId still exists → still open
        if deal_id in live_ids:
            continue

        # If dealId disappeared → CLOSED
        epic = trade.get("epic")
        direction = trade.get("side")
        size = float(trade.get("size"))
        entry_price = float(trade.get("entry_price"))

        # 4. Fetch market snapshot for exit price
        r = session.request("GET", f"{API_MARKET}/{epic}")
        if not r or r.status_code != 200:
            print(f"[SYNC] Market snapshot unavailable for {epic}")
            continue

        snapshot = r.json().get("snapshot", {})
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if bid is None or offer is None:
            print(f"[SYNC] No bid/offer for {epic}")
            continue

        # 5. Compute exit price based on direction
        exit_price = offer if direction.lower() == "buy" else bid

        # 6. Compute PnL manually
        if direction.lower() == "buy":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        print(f"[SYNC] CLOSED detected → epic={epic}, exit={exit_price}, pnl={pnl}")

        # 7. Log CLOSED trade
        log_closed_trade(
            ticker=trade.get("ticker"),
            epic=epic,
            deal_id=deal_id,
            side=direction,
            size=size,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            sl=trade.get("sl"),
            tp=trade.get("tp"),
            timeframe=trade.get("timeframe"),
            time_entered=trade.get("time_entered"),
            timestamp=timestamp()
        )
