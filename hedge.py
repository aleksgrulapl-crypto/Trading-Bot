"""One-click manual hedge: open the opposite-direction position for an
existing live position, using its exact size, no SL and a fixed GBP take-profit."""

import logging
from typing import Optional

import config
import session
from config import API_MARKET
from order import place_order
from trade_log import load_raw_log

logger = logging.getLogger("hedge")

HEDGE_TP_GBP = float(getattr(config, "HEDGE_TP_GBP", 15.0) or 15.0)


def _find_position(position_id: str) -> Optional[dict]:
    raw = session.get_positions(fresh=True) or []
    for p in session.enrich_positions(raw) or []:
        if str(p.get("dealId")) == str(position_id) or str(p.get("id")) == str(position_id):
            return p
    return None


def _tp_level(entry_price: float, size: float, buy: bool, currency: Optional[str]) -> float:
    """Price level at which the hedge makes HEDGE_TP_GBP (before spread)."""
    fx = 1.0 if str(currency or "USD").upper() == "GBP" else float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)
    distance = HEDGE_TP_GBP / (fx * size)
    level = entry_price + distance if buy else entry_price - distance
    return round(level, 2 if entry_price >= 10 else 4)


def hedge_position(position_id: str) -> dict:
    if not getattr(config, "HEDGING_ENABLED", False):
        return {"status": "error", "message": "hedging_disabled"}

    pos = _find_position(position_id)
    if not pos:
        return {"status": "error", "message": "position_not_found"}

    epic = pos.get("epic") or pos.get("ticker")
    side = pos.get("direction")
    try:
        size = float(pos.get("size"))
    except (TypeError, ValueError):
        size = 0.0
    if not epic or side not in ("Long", "Short") or size <= 0:
        return {"status": "error", "message": "invalid_position"}

    opposite = "Short" if side == "Long" else "Long"
    for t in load_raw_log() or []:
        if t.get("status") == "OPEN" and str(t.get("ticker") or t.get("epic")) == str(epic) and t.get("side") == opposite:
            return {"status": "error", "message": "already_hedged"}

    buy = opposite == "Long"
    snap = session.request("GET", f"{API_MARKET}/{epic}")
    try:
        snapshot = (snap.json() or {}).get("snapshot", {}) or {}
        entry = float(snapshot["offer"] if buy else snapshot["bid"])
    except Exception:
        return {"status": "error", "message": "price_unavailable"}

    tp = _tp_level(entry, size, buy, pos.get("currency"))
    timeframe = None
    for t in reversed(load_raw_log() or []):
        if t.get("dealId") and str(t.get("dealId")) == str(pos.get("dealId")):
            timeframe = t.get("timeframe")
            break

    result = place_order(epic, "BUY" if buy else "SELL", size, sl=None, tp=tp,
                         timeframe=timeframe, trade_source="hedge")
    if isinstance(result, dict) and result.get("status") == "error":
        return {"status": "error", "message": result.get("message") or "hedge_failed"}
    return {"status": "success", "message": f"Hedge opened: {'BUY' if buy else 'SELL'} {size} {epic}, TP {tp}."}
