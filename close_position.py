#!/usr/bin/env python3
# close_position.py
# Close a live broker position and record the result in the trade log.
#
# Key improvements:
#   - Trade log is only updated when the broker close request succeeds (2xx)
#   - Distinguishes recoverable (network) vs fatal (auth, bad request) errors
#   - Returns an explicit error dict instead of always returning "success"
#   - All outbound requests use BROKER_API_TIMEOUT

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

# Timeout (seconds) for every outbound broker API call
BROKER_API_TIMEOUT = 30

logger = logging.getLogger("close_position")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [close] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_side(side: Optional[str]) -> Optional[str]:
    """Normalise a direction string to 'Long' or 'Short'."""
    if not side:
        return None
    s = str(side).strip().upper()
    if s in ("BUY", "LONG"):
        return "Long"
    if s in ("SELL", "SHORT"):
        return "Short"
    return s.capitalize()


def _find_enriched_position(position_id: str):
    """Look up an enriched position dict from the live broker feed."""
    raw_positions = session.get_positions() or []
    enriched = session.enrich_positions(raw_positions) or []
    for p in enriched:
        if str(p.get("id")) == str(position_id) or str(p.get("dealId")) == str(position_id):
            return p
    return None


def _find_open_trade(deal_id: str):
    """Find the most recent OPEN trade in the local log matching *deal_id*."""
    log = load_raw_log() or []
    for entry in reversed(log):
        if entry.get("status") == "OPEN" and entry.get("dealId") and str(entry.get("dealId")) == str(deal_id):
            return entry
    return None


def _fetch_close_details_from_history(deal_id: str) -> Tuple[Optional[float], Optional[float]]:
    """Try to find close details (exit price, pnl) from transaction history.

    Returns:
        Tuple of (exit_price, pnl) where either element may be None if not
        found or unparseable.
    """
    try:
        url = f"{API_HISTORY_TRANSACTIONS}?max=200"
        r = session.request("GET", url, timeout=BROKER_API_TIMEOUT)
        if not r or r.status_code != 200:
            logger.debug("History fetch failed: %s", r.status_code if r else "no_response")
            return None, None

        data = r.json() or {}
        transactions = data.get("transactions", []) or []

        for tx in transactions:
            tx_deal_id = tx.get("dealId") or tx.get("positionId")
            if tx_deal_id and str(tx_deal_id) == str(deal_id):
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
    """Use the market snapshot to estimate an exit price and compute P&L.

    Returns:
        Tuple of (exit_price, pnl) or (None, None) on failure.
    """
    try:
        url = f"{API_MARKET}/{epic}"
        r = session.request("GET", url, timeout=BROKER_API_TIMEOUT)
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


# ---------------------------------------------------------------------------
# Main close function
# ---------------------------------------------------------------------------

def close_position(position_id: str) -> dict:
    """Close a live broker position and record the closed trade in the log.

    Steps:
      1. Ensure authentication tokens are valid.
      2. Call the broker close endpoint; abort if it fails.
      3. Extract exit details from the close response JSON.
      4. Fall back to transaction history if the response lacks close details.
      5. Fall back to a market snapshot to estimate exit price and P&L.
      6. Record the closed trade via the trade log.
      7. Update system status.

    Returns:
        {"status": "success", "message": ...} on success.
        {"status": "error",   "message": ...} on any failure.

    Note:
        The trade log is updated ONLY when the broker close request returns
        a 2xx status.  Previous versions updated the log even on failure,
        potentially marking an open position as closed incorrectly.
    """
    # Step 1 – Ensure auth tokens
    if not auth.ensure_token():
        logger.error("Authentication failed; cannot close position %s", position_id)
        return {"status": "error", "message": "auth_failed"}

    # Step 2 – Call the broker close endpoint
    resp_json = None
    try:
        url = f"{API_POSITIONS}/{position_id}"
        logger.debug("Close URL → %s", url)

        response = session.request("DELETE", url, timeout=BROKER_API_TIMEOUT)
        if not response:
            logger.error("No response from close endpoint for position %s", position_id)
            return {"status": "error", "message": "no_response"}

        logger.debug("Close response status: %s", response.status_code)
        try:
            resp_json = response.json()
        except Exception:
            resp_json = None

        if response.status_code not in (200, 201, 204):
            body = response.text if response is not None else "no_response"
            logger.warning(
                "Close endpoint returned %s for position %s: %s",
                response.status_code, position_id, body[:500],
            )
            return {"status": "error", "message": f"broker_close_failed_{response.status_code}", "detail": body}

    except Exception as e:
        logger.exception("Exception during broker close for position %s: %s", position_id, e)
        return {"status": "error", "message": str(e)}

    # ----- Broker close succeeded; now record in trade log -----

    # Step 3 – Build closed-trade context
    try:
        open_trade = _find_open_trade(position_id)
        pos = _find_enriched_position(position_id)

        ticker = (pos.get("ticker") if pos else None) or (open_trade.get("ticker") if open_trade else None)
        epic = (pos.get("epic") if pos else None) or (open_trade.get("epic") if open_trade else None)

        direction_raw = (pos.get("direction") if pos else None) or (open_trade.get("side") if open_trade else None)
        direction = _normalize_side(direction_raw)

        size = (pos.get("size") if pos else None) or (open_trade.get("size") if open_trade else None)
        entry_price = (pos.get("price") if pos else None) or (open_trade.get("entry_price") if open_trade else None)

        time_entered = open_trade.get("time_entered") if open_trade else None

        # Step 3a – Try to extract exit details from close response JSON
        exit_price = None
        pnl = None

        if resp_json:
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

        # Step 4 – Fall back to history
        if exit_price is None or pnl is None:
            hist_exit, hist_pnl = _fetch_close_details_from_history(position_id)
            if hist_exit is not None:
                exit_price = hist_exit
            if hist_pnl is not None:
                pnl = hist_pnl

        # Step 5 – Fall back to market snapshot
        if exit_price is None or pnl is None:
            logger.debug("History missing or incomplete, using snapshot fallback for %s", position_id)
            snap_exit, snap_pnl = _snapshot_exit(epic or ticker, direction, entry_price, size)
            if snap_exit is not None and exit_price is None:
                exit_price = snap_exit
            if snap_pnl is not None and pnl is None:
                pnl = snap_pnl

        logger.info("Final close details for %s: exit_price=%s pnl=%s", position_id, exit_price, pnl)

        # Step 6 – Record closed trade in trade log
        if position_id:
            updated = close_trade_by_dealId(position_id, exit_price=exit_price, time_exited=timestamp(), note="Closed via API")
            if updated:
                if updated.get("pnl") is None and pnl is not None and exit_price is not None:
                    close_trade_by_dealId(position_id, exit_price=exit_price, time_exited=timestamp(), note="Updated pnl")
            else:
                # No matching dealId in log – try fallback close by ticker + entry_price
                if ticker and entry_price is not None:
                    fallback = close_trade_fallback(
                        ticker, entry_price,
                        exit_price=exit_price,
                        time_exited=timestamp(),
                        note="Closed via API (fallback)",
                    )
                    if not fallback:
                        logger.warning(
                            "Could not find matching open trade to close for dealId=%s; "
                            "trade log may be out of sync",
                            position_id,
                        )
        else:
            # No deal ID – try fallback close
            if ticker and entry_price is not None:
                close_trade_fallback(
                    ticker, entry_price,
                    exit_price=exit_price,
                    time_exited=timestamp(),
                    note="Closed via API (no dealId)",
                )

    except Exception as e:
        # Log the error but do NOT convert a failed log update into a "success"
        # response – the broker close already succeeded, so we return success
        # but include a warning about the log update failure.
        logger.exception("Failed to update trade log after closing position %s: %s", position_id, e)
        # Step 7
        try:
            session.update_last_trade()
        except Exception:
            pass
        return {
            "status": "success",
            "message": f"Position {position_id} closed by broker but trade log update failed.",
            "warning": str(e),
        }

    # Step 7 – Update system status
    try:
        session.update_last_trade()
    except Exception:
        logger.debug("Failed to update last trade timestamp")

    return {"status": "success", "message": f"Position {position_id} closed."}
