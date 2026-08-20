# ============================
# SYNC CLOSED TRADES (FINAL — SIZE + DISAPPEARANCE, NO AUTO CLOSES)
# ============================

import time
import session
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_MARKET

# Memory of last 2 raw position snapshots (dealIds)
_last_raw_1 = set()
_last_raw_2 = set()

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
    global _last_raw_1, _last_raw_2

    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    raw_positions = session.get_positions()

    if raw_positions is None:
        print("[SYNC] Positions unavailable → skipping")
        return

    # Build raw dealId + size map from raw positions
    raw_ids = set()
    raw_size_map = {}

    for item in raw_positions:
        pos = item.get("position", {})
        deal_id = pos.get("dealId")
        if deal_id is None:
            continue
        deal_id_str = str(deal_id)
        raw_ids.add(deal_id_str)
        raw_size_map[deal_id_str] = pos.get("size")

    for trade in open_trades:
        deal_id = str(trade.get("dealId"))

        # Already logged
        if deal_id in _last_close_cache:
            continue

        # If we still see the position with non-zero size → still open
        size = raw_size_map.get(deal_id, None)
        try:
            size_val = float(size) if size is not None else None
        except Exception:
            size_val = None

        if size_val is not None and size_val > 0:
            continue

        # CLOSE CONDITION #1 — size becomes 0
        size_zero = size_val == 0

        # CLOSE CONDITION #2 — disappearance confirmed over last snapshots
        disappeared = (
            deal_id not in raw_ids
            and (deal_id in _last_raw_1 or deal_id in _last_raw_2)
        )

        # If neither condition is true → skip
        if not (size_zero or disappeared):
            continue

        epic = trade.get("epic")
        direction = trade.get("side")
        trade_size = float(trade.get("size"))
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
            pnl = (exit_price - entry_price) * trade_size
        else:
            pnl = (entry_price - exit_price) * trade_size

        print(f"[SYNC] CLOSED detected → epic={epic}, exit={exit_price}, pnl={pnl}")

        log_closed_trade(
            ticker=trade.get("ticker"),
            epic=epic,
            deal_id=deal_id,
            side=direction,
            size=trade_size,
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
    _last_raw_2 = _last_raw_1
    _last_raw_1 = raw_ids.copy()
