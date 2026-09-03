# sync_closed_trades.py
# ============================
# SYNC CLOSED TRADES (FINAL — SIZE + DISAPPEARANCE, NO DUPLICATES)
# ============================

import time
import logging
from typing import Optional, Tuple

import session
import config
from trade_log import (
    load_raw_log,
    close_trade_by_dealId,
    close_trade_fallback,
)
from utils import timestamp
from datetime import datetime, timezone

logger = logging.getLogger("sync_closed_trades")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [sync] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)

# small in-memory caches to avoid spamming market snapshots and to detect disappearance
_last_raw_1 = set()
_last_raw_2 = set()
_last_close_cache = {}
_snapshot_cache = {}
# tracks how many consecutive polls a dealId was absent from live positions
_absent_count: dict = {}


def _now() -> float:
    return time.time()


def _parse_entry_time_to_utc_naive(value) -> Optional[datetime]:
    """
    Best-effort parse of a trade's time_entered value (an ISO-8601 string,
    possibly UK-timezone-aware, or one of trade_log's alternate human formats)
    into a naive UTC datetime, so it can be diffed against datetime.utcnow()
    to compute the trade's age in seconds.
    """
    if not value:
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H.%M.%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _trade_age_seconds(trade) -> Optional[float]:
    """Return how many seconds ago *trade* was opened, or None if unknown."""
    entered = _parse_entry_time_to_utc_naive(trade.get("time_entered"))
    if entered is None:
        return None
    try:
        return (datetime.utcnow() - entered).total_seconds()
    except Exception:
        return None


def _parse_broker_timestamp(value) -> Optional[str]:
    """
    Normalize a Capital.com transaction timestamp (Unix seconds/milliseconds
    or an ISO string, with or without a trailing "Z") into the
    "YYYY-MM-DD HH:MM:SS" string format understood by trade_log's timestamp
    parsing (_parse_iso_like), or None if *value* is missing/unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            dt = datetime.utcfromtimestamp(value / 1000.0 if value > 1e12 else value)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", ""))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    return None


def _fetch_exit_from_history(deal_id: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Query Capital.com transaction history for the actual close level, P&L, and
    close time. Returns (exit_price, pnl, close_time) — any may be None if not
    found. *close_time* is the broker's real close timestamp (parsed from the
    transaction's closeDate/date field), which should be preferred over "now"
    (the moment our polling loop happened to detect the close) so that
    time_exited – and the analytics derived from it – reflect when the
    position was actually closed rather than when we noticed.
    """
    try:
        r = session.request("GET", f"{config.API_HISTORY_TRANSACTIONS}?max=200")
        if not r or r.status_code != 200:
            return None, None, None
        transactions = (r.json() or {}).get("transactions", []) or []
        for tx in transactions:
            tx_id = tx.get("dealId") or tx.get("positionId")
            if tx_id and str(tx_id) == str(deal_id):
                ep = tx.get("closeLevel") or tx.get("level") or tx.get("price")
                pnl = tx.get("profitAndLoss") or tx.get("pnl") or tx.get("profit") or tx.get("profitLoss")
                close_ts_raw = tx.get("closeDate") or tx.get("dateUtc") or tx.get("date")
                try:
                    ep = float(ep) if ep is not None else None
                except Exception:
                    ep = None
                try:
                    pnl = float(pnl) if pnl is not None else None
                except Exception:
                    pnl = None
                close_time = _parse_broker_timestamp(close_ts_raw)
                return ep, pnl, close_time
    except Exception as e:
        logger.debug("_fetch_exit_from_history error for %s: %s", deal_id, e)
    return None, None, None


def _confirm_position_gone(deal_id: str) -> Optional[bool]:
    """Directly query the single-position endpoint for *deal_id* to confirm it
    is really gone from the broker, rather than trusting a momentary gap in
    the aggregate /positions list (which can lag or drop entries transiently
    due to pagination/rate limiting/eventual consistency).

    Returns True if confirmed closed/absent, False if the broker still
    reports it as an open position, or None if the check was inconclusive
    (e.g. network error) — callers should treat None as "not yet confirmed"
    and avoid closing the trade based on it.
    """
    try:
        r = session.request("GET", f"{config.API_POSITIONS}/{deal_id}")
    except Exception as e:
        logger.debug("_confirm_position_gone: request error for %s: %s", deal_id, e)
        return None
    if r is None:
        return None
    if r.status_code == 200:
        return False
    if r.status_code in (400, 404):
        return True
    # Any other status (rate limit, auth hiccup, 5xx, ...) is inconclusive.
    return None


def get_snapshot(epic: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (bid, offer) for epic. Cache for a few seconds to avoid rate limits.
    """
    if not epic:
        return None, None

    now = _now()
    cached = _snapshot_cache.get(epic)
    if cached:
        bid, offer, ts = cached
        if now - ts < 3:
            return bid, offer

    r = session.request("GET", f"{config.API_MARKET}/{epic}")
    if not r or r.status_code != 200:
        logger.debug("get_snapshot: market request failed for %s", epic)
        return None, None

    try:
        snapshot = (r.json() or {}).get("snapshot", {}) or {}
    except Exception:
        logger.debug("get_snapshot: failed to parse JSON for %s", epic)
        return None, None

    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        logger.debug("get_snapshot: missing bid/offer for %s", epic)
        return None, None

    _snapshot_cache[epic] = (bid, offer, now)
    return bid, offer


def sync_closed_trades():
    """
    Detect closed trades by:
      - size dropping to zero for a dealId
      - or dealId disappearing from live positions across two consecutive snapshots
      - or dealId absent for 3+ consecutive polls (catches positions closed during bot restart)
    Exit price is sourced from Capital.com transaction history first, with snapshot as fallback.
    """
    global _last_raw_1, _last_raw_2

    log = load_raw_log() or []

    # set of already-closed dealIds in the log
    closed_ids = {
        str(t.get("dealId"))
        for t in log
        if t.get("status") == "CLOSED" and t.get("dealId") is not None
    }

    open_trades = [t for t in log if t.get("status") == "OPEN"]

    if not open_trades:
        logger.debug("sync_closed_trades: no open trades to check")
        return

    raw_positions = session.get_positions()
    if raw_positions is None:
        logger.debug("sync_closed_trades: positions unavailable, skipping")
        return

    if raw_positions == []:
        logger.debug("sync_closed_trades: empty positions snapshot, skipping close detection")
        return

    # Build maps from live positions
    raw_ids = set()
    raw_size_map = {}

    for item in raw_positions:
        pos = item.get("position") or {}
        deal_id = pos.get("dealId")
        if deal_id is None:
            continue
        did = str(deal_id)
        raw_ids.add(did)
        # size may be None or non-numeric; keep raw value for later parsing
        raw_size_map[did] = pos.get("size")

    # Update consecutive-absence counter for open trades
    for trade in open_trades:
        did = trade.get("dealId")
        if did is None:
            continue
        did = str(did)
        if did in closed_ids:
            _absent_count.pop(did, None)
        elif did not in raw_ids:
            _absent_count[did] = _absent_count.get(did, 0) + 1
        else:
            _absent_count.pop(did, None)

    # Iterate open trades and detect closes
    for trade in open_trades:
        deal_id = trade.get("dealId")
        if deal_id is None:
            # cannot detect disappearance by dealId; skip
            continue
        deal_id = str(deal_id)

        # skip if already processed recently or already closed
        if deal_id in _last_close_cache or deal_id in closed_ids:
            continue

        # check live size for this dealId
        size_raw = raw_size_map.get(deal_id, None)
        try:
            size_val = float(size_raw) if size_raw is not None else None
        except Exception:
            size_val = None

        # if size is present and > 0, still open
        if size_val is not None and size_val > 0:
            continue

        # disappearance: was in a previous snapshot OR absent 3+ consecutive polls
        disappeared = (
            deal_id not in raw_ids
            and (
                deal_id in _last_raw_1
                or deal_id in _last_raw_2
                or _absent_count.get(deal_id, 0) >= 3
            )
        )

        size_zero = (size_val == 0)

        if not (size_zero or disappeared):
            # not a close candidate
            continue

        if disappeared and not size_zero:
            # A just-opened position can transiently fail to appear in the
            # aggregate /positions list (or even 404 from the single-position
            # endpoint below) for a few seconds while the broker propagates
            # the new position, which would otherwise cause the trade to be
            # wrongly auto-closed moments after it was opened. Give freshly
            # opened trades a grace period before trusting disappearance-based
            # signals; a genuine size==0 report is still honoured immediately
            # since it comes directly from the broker for that dealId.
            age_seconds = _trade_age_seconds(trade)
            grace_period = float(getattr(config, "AUTOCLOSE_GRACE_PERIOD_SECONDS", 60) or 0)
            if age_seconds is not None and age_seconds < grace_period:
                logger.debug(
                    "sync_closed_trades: %s opened %.0fs ago (< %.0fs grace period); ignoring disappearance likely due to broker propagation delay",
                    deal_id, age_seconds, grace_period,
                )
                _absent_count.pop(deal_id, None)
                continue

        if disappeared and not size_zero:
            # The aggregate /positions list is only a hint here – it can
            # transiently omit a genuinely-still-open position (broker-side
            # eventual consistency, pagination, rate limiting), which would
            # otherwise cause a real open trade to be marked CLOSED (using a
            # stale/incorrect exit price) seconds after it was opened, while
            # the position keeps trading live at the broker. Confirm directly
            # against the single-position endpoint before finalizing a close
            # based on absence alone.
            confirmed_gone = _confirm_position_gone(deal_id)
            if confirmed_gone is False:
                logger.debug("sync_closed_trades: %s still reported open by broker; ignoring transient absence from positions list", deal_id)
                _absent_count.pop(deal_id, None)
                continue
            if confirmed_gone is None:
                logger.debug("sync_closed_trades: could not confirm %s is closed (inconclusive check); deferring", deal_id)
                continue

        epic = trade.get("epic") or trade.get("ticker")
        direction = (trade.get("side") or "").lower()
        try:
            trade_size = float(trade.get("size") or 0)
        except Exception:
            trade_size = 0.0
        try:
            entry_price = float(trade.get("entry_price") or 0)
        except Exception:
            entry_price = 0.0

        # Step 1: try to get actual close price/time from Capital.com transaction history
        exit_price, broker_pnl, broker_close_time = _fetch_exit_from_history(deal_id)
        pnl = broker_pnl

        # Step 2: fall back to market snapshot if history didn't have it
        if exit_price is None:
            bid, offer = get_snapshot(epic)
            if bid is None or offer is None:
                logger.debug("sync_closed_trades: snapshot unavailable for %s, skipping", epic)
                continue

            if direction == "long":
                exit_price = float(bid)
            else:
                exit_price = float(offer)

        # Step 3: compute pnl from exit_price if history didn't provide it
        if pnl is None and exit_price is not None:
            if direction == "long":
                pnl = round((exit_price - entry_price) * trade_size, 2)
            else:
                pnl = round((entry_price - exit_price) * trade_size, 2)

        logger.info("sync_closed_trades: CLOSED detected → epic=%s dealId=%s exit=%s pnl=%s (broker_pnl=%s) close_time=%s",
                    epic, deal_id, exit_price, pnl, broker_pnl, broker_close_time)

        # Prefer the broker's actual close time (from transaction history) over
        # "now" (the moment this poll happened to detect the close), so that
        # time_exited – and any analytics derived from it (e.g. duration,
        # trades-per-hour) – reflect the true close rather than detection lag.
        time_exited = broker_close_time or timestamp()

        # Prefer marking by dealId; if trade_log has no matching dealId, fallback by ticker+entry_price
        # Pass broker_pnl (not the possibly-estimated `pnl`) so trade_log only overrides its own
        # computed figure when Capital.com's transaction history actually reported one.
        updated = close_trade_by_dealId(deal_id, exit_price=exit_price, time_exited=time_exited, note="Closed via sync", pnl=broker_pnl)
        if not updated:
            # fallback: try to close by ticker + entry_price
            fallback = close_trade_fallback(trade.get("ticker") or epic, entry_price, exit_price=exit_price, time_exited=time_exited, note="Closed via sync (fallback)", pnl=broker_pnl)
            if not fallback:
                logger.warning("sync_closed_trades: could not close trade for dealId=%s (no matching open trade found)", deal_id)
            else:
                logger.debug("sync_closed_trades: fallback close succeeded for dealId=%s", deal_id)
        else:
            logger.debug("sync_closed_trades: closed trade recorded for dealId=%s", deal_id)

        # mark as processed to avoid duplicate handling in same run
        _last_close_cache[deal_id] = _now()
        _absent_count.pop(deal_id, None)

    # rotate raw id history for disappearance detection
    _last_raw_2 = _last_raw_1
    _last_raw_1 = raw_ids.copy()

    # prune _last_close_cache entries older than a short TTL (e.g., 300s)
    ttl = 300
    now = _now()
    keys_to_delete = [k for k, ts in _last_close_cache.items() if (now - ts) > ttl]
    for k in keys_to_delete:
        _last_close_cache.pop(k, None)
