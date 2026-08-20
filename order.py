# order.py
# ============================
# ORDER MODULE (FINAL — CORRECT CONFIRMS ENDPOINT + STABLE DEALID MAPPING)
# ============================

import time
import logging
from typing import Optional

import session
from auth import auth
from config import API_POSITIONS, API_MARKET, API_BASE, DEBUG_LOGS
from utils import timestamp
from trade_log import append_open_trade

logger = logging.getLogger("order")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [order] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if DEBUG_LOGS else logging.INFO)


def _normalize_direction(direction: Optional[str]) -> str:
    if not direction:
        return "BUY"
    d = str(direction).strip().lower()
    if d in ("buy", "b", "long"):
        return "BUY"
    if d in ("sell", "s", "short"):
        return "SELL"
    return direction.upper()


def place_order(epic: str, direction: str, size: float, sl: Optional[float] = None, tp: Optional[float] = None, timeframe: Optional[str] = None) -> dict:
    """
    Place a BUY/SELL market order and log an OPEN trade.
    Uses Capital.com's confirms endpoint to map dealReference -> dealId when available.
    Returns a dict with status and details.
    """
    # Ensure authentication
    if not auth.ensure_token():
        logger.error("Authentication failed; cannot place order")
        return {"status": "error", "message": "auth_failed"}

    # 1) MARKET SNAPSHOT
    try:
        snap_resp = session.request("GET", f"{API_MARKET}/{epic}")
        if not snap_resp or snap_resp.status_code != 200:
            logger.error("Market snapshot unavailable for %s", epic)
            return {"status": "error", "message": "Market snapshot unavailable"}

        snap_json = {}
        try:
            snap_json = snap_resp.json() or {}
        except Exception:
            logger.debug("Failed to parse market snapshot JSON for %s", epic)

        snapshot = snap_json.get("snapshot", {}) or {}
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if bid is None or offer is None:
            logger.error("Market prices unavailable for %s", epic)
            return {"status": "error", "message": "Price unavailable"}

        dir_norm = _normalize_direction(direction)
        entry_price = float(offer) if dir_norm == "BUY" else float(bid)

    except Exception as e:
        logger.exception("Exception while fetching market snapshot: %s", e)
        return {"status": "error", "message": "snapshot_error"}

    # 2) BUILD ORDER PAYLOAD
    try:
        payload = {
            "epic": epic,
            "direction": dir_norm,
            "size": float(size),
            "orderType": "MARKET",
            "level": None,
            "guaranteedStop": False,
        }

        if sl is not None:
            payload["stopLevel"] = float(sl)
        if tp is not None:
            payload["profitLevel"] = float(tp)

        logger.debug("Order payload: %s", payload)
    except Exception as e:
        logger.exception("Invalid order parameters: %s", e)
        return {"status": "error", "message": "invalid_parameters"}

    # 3) SEND ORDER
    try:
        response = session.request("POST", API_POSITIONS, json=payload)
        if not response or response.status_code >= 400:
            body = response.text if response else "no_response"
            logger.error("Order failed for %s (%s): %s", epic, direction, body)
            return {"status": "error", "message": "Order failed", "detail": body}

        try:
            data = response.json() or {}
        except Exception:
            data = {}
            logger.debug("Failed to parse order response JSON")

        deal_ref = data.get("dealReference") or data.get("deal_reference") or data.get("dealRef")
        logger.info("Order placed: %s %s @ %s (size %s) dealReference=%s", direction.upper(), epic, entry_price, size, deal_ref)

    except Exception as e:
        logger.exception("Exception while sending order: %s", e)
        return {"status": "error", "message": "order_request_failed"}

    # 4) DEALID MAPPING VIA CONFIRMS ENDPOINT
    real_deal_id = None
    if deal_ref:
        confirms_url = f"{API_BASE}/api/v1/confirms/{deal_ref}"
        backoff = 0.2
        for attempt in range(10):
            time.sleep(backoff)
            try:
                confirm = session.request("GET", confirms_url)
                if not confirm:
                    backoff = min(backoff * 1.5, 2.0)
                    continue
                if confirm.status_code == 200:
                    try:
                        body = confirm.json() or {}
                    except Exception:
                        body = {}
                    # try common keys
                    real_deal_id = body.get("dealId") or body.get("deal_id") or body.get("dealId")
                    if real_deal_id:
                        break
                else:
                    logger.debug("Confirm attempt %d failed: %s %s", attempt + 1, confirm.status_code, getattr(confirm, "text", ""))
            except Exception as e:
                logger.debug("Confirm attempt exception: %s", e)
            backoff = min(backoff * 1.5, 2.0)

        if real_deal_id:
            logger.info("Mapped dealReference -> dealId: %s", real_deal_id)
        else:
            logger.warning("Could not map dealReference -> dealId via confirms for dealReference=%s", deal_ref)
    else:
        logger.warning("No dealReference returned by order response; logging open trade without dealId")

    # 5) LOG OPEN TRADE (canonical append_open_trade)
    try:
        ts = timestamp()
        trade_payload = {
            "dealId": real_deal_id,
            "ticker": epic,
            "epic": epic,
            "side": "Long" if dir_norm == "BUY" else "Short",
            "size": float(size),
            "entry_price": float(entry_price),
            "time_entered": ts,
            "exit_price": None,
            "time_exited": None,
            "pnl": None,
            "status": "OPEN",
            "notes": f"sl={sl}; tp={tp}; timeframe={timeframe}; dealReference={deal_ref}"
        }
        appended = append_open_trade(trade_payload)
        logger.debug("Logged open trade: %s", appended)
    except Exception as e:
        logger.exception("Failed to log open trade: %s", e)

    # 6) Update last trade timestamp
    try:
        session.update_last_trade()
    except Exception:
        logger.debug("Failed to update last trade timestamp")

    return {
        "status": "ok",
        "dealReference": deal_ref,
        "dealId": real_deal_id,
        "price": entry_price,
    }
