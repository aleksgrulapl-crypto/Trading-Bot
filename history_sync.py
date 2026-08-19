# ============================
# SYNC CLOSED TRADES (STABLE VERSION)
# ============================

import time
import session
from trade_log import load_raw_log, log_closed_trade
from utils import timestamp
from config import API_MARKET

_last_close_cache = {}       # dealId → True (prevents double logging)
_snapshot_cache = {}         # epic → (bid, offer, ts)


def get_snapshot(epic):
    """
    Cached market snapshot to avoid 429 rate limits.
    Cache lifetime: 3 seconds.
    """
    now = time.time()

    if epic in _snapshot_cache:
        bid, offer, ts = _snapshot_cache[epic]
        if now - ts < 3:
            return bid, offer

    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r or r.status_code != 200:
        print(f"[SYNC] Market snapshot unavailable for {epic}")
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
    Detect CLOSED trades by disappearance.
    Compute exit price using cached market snapshot.
    Prevent double logging.
    """

    log = load_raw_log()
    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        return

    raw_positions = session.get_positions() or []
    enriched_positions = session.enrich_positions(raw_positions) or []
    live_ids = set(str(p.get("id")) for p in enriched_positions)

    for trade in open_trades:
        deal_id = str(trade.get("dealId"))

        # Already logged → skip
        if deal_id in _last_close_cache:
            continue

        # Still open → skip
        if deal_id in live_ids:
            continue

        # CLOSED detected
        epic = trade.get("epic")
        direction = trade.get("side")
        size = float(trade.get("size"))
        entry_price = float(trade.get("entry_price"))

        bid, offer = get_snapshot(epic)
        if bid is None or offer is None:
            continue

        exit_price = offer if direction.lower() == "buy" else bid

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

        # Prevent double logging
        _last_close_cache[deal_id] = True
