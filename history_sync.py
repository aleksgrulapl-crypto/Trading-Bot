# ============================
# SYNC CLOSED TRADES (PATCHED + STABLE)
# ============================

import time
import session
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_MARKET

_last_positions = set()       # previous cycle positions
_last_close_cache = {}        # dealId → True (debounce)
_snapshot_cache = {}          # epic → (bid, offer, ts)


def get_snapshot(epic):
    """
    Cached market snapshot to avoid 429 rate limits.
    Cache lifetime: 3 seconds.
    """
    now = time.time()

    # Use cached snapshot if fresh
    if epic in _snapshot_cache:
        bid, offer, ts = _snapshot_cache[epic]
        if now - ts < 3:
            return bid, offer

    # Fetch fresh snapshot
    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r or r.status_code != 200:
        print(f"[SYNC] Snapshot unavailable for {epic}")
        return None, None

    snapshot = r.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        print(f"[SYNC] No bid/offer for {epic}")
        return None, None

    _snapshot_cache[epic] = (bid, offer, now)
    return bid, offer


def sync_closed_trades():
    """
    Detect CLOSED trades safely:
    - Protect against rate limits
    - Protect against empty /positions
    - Protect against instant open→close bugs
    - Protect against snapshot failures
    - Prevent duplicate CLOSED logs
    """

    global _last_positions

    # Load trade log
    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    # Fetch current positions
    raw_positions = session.get_positions()

    # Rate-limit or empty response → skip cycle
    if raw_positions is None or raw_positions == []:
        print("[SYNC] Positions unavailable (rate limit) → skipping")
        return

    enriched_positions = session.enrich_positions(raw_positions) or []
    current_positions = set(str(p.get("id")) for p in enriched_positions)

    # Process each open trade
    for trade in open_trades:
        deal_id = str(trade.get("dealId"))

        # Already logged → skip
        if deal_id in _last_close_cache:
            continue

        # Still open → skip
        if deal_id in current_positions:
            continue

        # Only treat disappearance as close if it existed previously
        if deal_id not in _last_positions:
            continue

        # Prevent instant open→close bugs
        time_entered = trade.get("time_entered")
        if timestamp() - time_entered < 3:
            print("[SYNC] Trade too new → ignoring disappearance")
            continue

        # Fetch snapshot
        epic = trade.get("epic")
        direction = trade.get("side")
        size = float(trade.get("size"))
        entry_price = float(trade.get("entry_price"))

        bid, offer = get_snapshot(epic)
        if bid is None or offer is None:
            print("[SYNC] Snapshot unavailable → skipping close detection")
            continue

        # Compute exit price
        exit_price = offer if direction.lower() == "buy" else bid

        # Compute PnL
        if direction.lower() == "buy":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        print(f"[SYNC] CLOSED detected → epic={epic}, exit={exit_price}, pnl={pnl}")

        # Log CLOSED trade
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
            time_entered=time_entered,
            timestamp=timestamp()
        )

        # Debounce
        _last_close_cache[deal_id] = True

    # Update previous positions
    _last_positions = current_positions.copy()
