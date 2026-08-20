# ============================
# SYNC CLOSED TRADES (FINAL — DISAPPEARANCE + UPL FALLBACK)
# ============================

import time
import session
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_MARKET

# Memory of last 2 snapshots to avoid rate-limit false closes
_last_positions_1 = set()
_last_positions_2 = set()

_last_close_cache = {}
_snapshot_cache = {}


def get_snapshot(epic):
    now = time.time()

    if epic in _snapshot_cache:
        bid, offer, ts = _snapshot_cache[epic]
        if now - ts < 3:
            return bid, offer

    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r or r.status_code != 200:
        return None, None

    snapshot = r.json().get("snapshot", {})
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        return None, None

    _snapshot_cache[epic] = (bid, offer, now)
    return bid, offer


def sync_closed_trades():
    global _last_positions_1, _last_positions_2

    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    raw_positions = session.get_positions()

    # If API fails → skip
    if raw_positions is None:
        print("[SYNC] Positions unavailable → skipping")
        return

    enriched_positions = session.enrich_positions(raw_positions) or []
    current_positions = set(str(p.get("id")) for p in enriched_positions)

    # Build UPL lookup
    upl_map = {str(p.get("id")): p.get("profit") for p in enriched_positions}

    for trade in open_trades:
        deal_id = str(trade.get("dealId"))

        # Already logged
        if deal_id in _last_close_cache:
            continue

        # Still open
        if deal_id in current_positions:
            continue

        # ❗ CLOSE CONDITION #1 — Disappearance (with 2-snapshot confirmation)
        disappeared = (
            deal_id not in current_positions
            and (deal_id in _last_positions_1 or deal_id in _last_positions_2)
        )

        # ❗ CLOSE CONDITION #2 — UPL fallback (Capital.com stale position bug)
        upl_value = upl_map.get(deal_id, None)
        upl_closed = upl_value is None

        # If neither condition is true → skip
        if not disappeared and not upl_closed:
            continue

        # Fetch snapshot
        epic = trade.get("epic")
        direction = trade.get("side")
        size = float(trade.get("size"))
        entry_price = float(trade.get("entry_price"))

        bid, offer = get_snapshot(epic)
        if bid is None or offer is None:
            print("[SYNC] Snapshot unavailable → skipping")
            continue

        # Correct side
        if direction.lower() == "buy":
            exit_price = bid
        else:
            exit_price = offer

        # Compute PnL
        if direction.lower() == "buy":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        print(f"[SYNC] CLOSED detected → epic={epic}, exit={exit_price}, pnl={pnl}")

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

        _last_close_cache[deal_id] = True

    # Shift snapshots
    _last_positions_2 = _last_positions_1
    _last_positions_1 = current_positions.copy()
