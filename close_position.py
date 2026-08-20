# close_position.py
# ============================
# CLOSE POSITION MODULE (FINAL — HISTORY + SNAPSHOT + NORMALIZED SIDE)
# ============================

import logging
from typing import Optional, Tuple

import session
from auth import auth
from trade_log import (
    load_raw_log,
    close_trade_by_dealId,
    close_trade_fallback,
)
from utils import timestamp
from config import API_POSITIONS, API_HISTORY_TRANSACTIONS, API_MARKET

logger = logging.getLogger("close_position")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [close] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def _normalize_side(side: Optional[str]) -> Optional[str]:
    if not side:
        return None
    s = str(side).strip().upper()
    if s in ("BUY", "LONG"):
        return "Long"
    if s in ("SELL", "SHORT"):
        return "Short"
    return s.capitalize()


def _find_enriched_position(position_id: str):
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions) or []
    for p in enriched:
        if str(p.get("id")) == str(position_id) or str(p.get("dealId")) == str(position_id):
            return p
    return None


def _find_open_trade(deal_id: str):
    log = load_raw_log() or []
    # search from newest to oldest
    for entry in reversed(log):
        if entry.get("status") == "OPEN" and entry.get("dealId") and str(entry.get("dealId")) == str(deal_id):
            return entry
    return None


def _fetch_close_details_from_history(deal_id: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Try to find close details (exit price, pnl) from transaction history.
    Returns (exit_price, pnl) or (None, None).
    """
    try:
        url = f"{API_HISTORY_TRANSACTIONS}?max=200"
        r = session.request("GET", url)
        if not r or r.status_code != 200:
            logger.debug("History fetch failed: %s", r.status_code if r else "no_response")
            return None, None

        data = r.json() or {}
        transactions = data.get("transactions", []) or []

        for tx in transactions:
            tx_deal_id = tx.get("dealId") or tx.get("positionId")
            if tx_deal_id and str(tx_deal_id) == str(deal_id):
                # try common fields
                exit_price = tx.get("closeLevel") or tx.get("level") or tx.get("price")
                pnl = tx.get("profitAndLoss") or tx.get("pnl") or tx.get("profit")
                try:
                    exit_price = float(exit_price) if exit_price is not None else None
                except Exception:
                    exit_price = None
                try:
                    pnl = float(pnl) if pnl is not None else None
                except Exception:
                    pnl = None
                return exit_price, pnl

        return None, None

    except Exception as e:
        logger.exception("Exception while reading history: %s", e)
        return None, None


def _snapshot_exit(epic: str, direction: str, entry_price, size) -> Tuple[Optional[float], Optional[float]]:
    """
    Use market snapshot to estimate exit price and compute pnl.
    Returns (exit_price, pnl) or (None, None).
    """
    try:
        url = f"{API_MARKET}/{epic}"
        r = session.request("GET", url)
        if not r or r.status_code != 200:
            logger.debug("Snapshot fetch failed: %s", r.status_code if r else "no_response")
            return None, None

        snapshot = (r.json() or {}).get("snapshot", {}) or {}
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if bid is None or offer is None:
            logger.debug("Snapshot missing bid/offer")
            return None, None

        try:
            entry_price = float(entry_price)
            size = float(size)
        except Exception:
            logger.debug("Invalid numeric entry_price/size for snapshot fallback")
            return None, None

        d = _normalize_side(direction)
        if d == "Long":
            exit_price = float(bid)
            pnl = (exit_price - entry_price) * size
        else:
            exit_price = float(offer)
            pnl = (entry_price - exit_price) * size

        return exit_price, round(float(pnl), 2)

    except Exception as e:
        logger.exception("Snapshot exit failed: %s", e)
        return None, None


# ---------------------------------------------------------
# MAIN CLOSE FUNCTION
# ---------------------------------------------------------

def close_position(position_id: str) -> dict:
    """
    Close a live position and record the closed trade in the trade log.
    Steps:
      1) Ensure auth
      2) Call broker close endpoint
      3) Attempt to extract exit details from response or history
      4) Fallback to market snapshot to estimate exit price/pnl
      5) Record closed trade via trade_log.close_trade_by_dealId or fallback
    """
    # Ensure we have tokens
    if not auth.ensure_token():
        logger.error("Authentication failed; cannot close position")
        return {"status": "error", "message": "auth_failed"}

    # 1) Call Capital.com close endpoint
    try:
        url = f"{API_POSITIONS}/{position_id}/close"
        logger.debug("Close URL → %s", url)

        response = session.request("POST", url)
        if not response:
            logger.error("No response from close endpoint")
            return {"status": "error", "message": "no_response"}

        logger.debug("Close response status: %s", response.status_code)
        # try to parse JSON body for details
        resp_json = None
        try:
            resp_json = response.json()
        except Exception:
            resp_json = None

        if response.status_code != 200:
            # return error body if available
            body = response.text if response is not None else "no_response"
            logger.warning("Close endpoint returned non-200: %s", body)
            return {"status": "error", "message": body}

    except Exception as e:
        logger.exception("Exception during close: %s", e)
        return {"status": "error", "message": str(e)}

    # 2) Build closed-trade record context
    try:
        open_trade = _find_open_trade(position_id)
        pos = _find_enriched_position(position_id)

        ticker = (pos.get("ticker") if pos else None) or (open_trade.get("ticker") if open_trade else None)
        epic = (pos.get("epic") if pos else None) or (open_trade.get("epic") if open_trade else None)

        direction_raw = (pos.get("direction") if pos else None) or (open_trade.get("side") if open_trade else None)
        direction = _normalize_side(direction_raw)

        size = (pos.get("size") if pos else None) or (open_trade.get("size") if open_trade else None)
        entry_price = (pos.get("price") if pos else None) or (open_trade.get("entry_price") if open_trade else None)

        sl = open_trade.get("sl") if open_trade else None
        tp = open_trade.get("tp") if open_trade else None
        timeframe = open_trade.get("timeframe") if open_trade else None
        time_entered = open_trade.get("time_entered") if open_trade else None

        # 3) Try to extract exit details from close response JSON first
        exit_price = None
        pnl = None

        if resp_json:
            # Common fields that might appear in confirm/close responses
            # Try multiple keys safely
            exit_price = resp_json.get("closeLevel") or resp_json.get("level") or resp_json.get("price") or resp_json.get("exitPrice")
            pnl = resp_json.get("profitAndLoss") or resp_json.get("pnl") or resp_json.get("profit")
            try:
                exit_price = float(exit_price) if exit_price is not None else None
            except Exception:
                exit_price = None
            try:
                pnl = float(pnl) if pnl is not None else None
            except Exception:
                pnl = None

        # 4) If missing, try history
        if exit_price is None or pnl is None:
            hist_exit, hist_pnl = _fetch_close_details_from_history(position_id)
            if hist_exit is not None:
                exit_price = hist_exit
            if hist_pnl is not None:
                pnl = hist_pnl

        # 5) Snapshot fallback if still missing
        if exit_price is None or pnl is None:
            logger.debug("History missing or incomplete, using snapshot fallback")
            snap_exit, snap_pnl = _snapshot_exit(epic or ticker, direction, entry_price, size)
            if snap_exit is not None and exit_price is None:
                exit_price = snap_exit
            if snap_pnl is not None and pnl is None:
                pnl = snap_pnl

        logger.info("Final close details for %s: exit_price=%s pnl=%s", position_id, exit_price, pnl)

        # 6) Record closed trade in trade log
        # Prefer to mark by dealId if present; otherwise use fallback close by ticker+entry_price
        if position_id:
            updated = close_trade_by_dealId(position_id, exit_price=exit_price, time_exited=timestamp(), note="Closed via API")
            if updated:
                # If pnl was computed externally and trade_log didn't compute it (e.g., no exit_price), ensure it's set
                if updated.get("pnl") is None and pnl is not None:
                    # update the record by closing again with exit_price (trade_log will compute pnl)
                    close_trade_by_dealId(position_id, exit_price=exit_price, time_exited=timestamp(), note="Updated pnl")
            else:
                # No matching dealId in log — try fallback close by ticker+entry_price
                if ticker and entry_price is not None:
                    fallback = close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=timestamp(), note="Closed via API (fallback)")
                    if not fallback:
                        logger.warning("Could not find matching open trade to close for dealId=%s; created no record", position_id)
        else:
            # No deal id — try fallback close
            if ticker and entry_price is not None:
                close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=timestamp(), note="Closed via API (no dealId)")

    except Exception as e:
        logger.exception("Failed to log CLOSED trade: %s", e)

    # 7) Update system status
    try:
        session.update_last_trade()
    except Exception:
        logger.debug("Failed to update last trade timestamp")

    return {"status": "success", "message": f"Position {position_id} closed."}
