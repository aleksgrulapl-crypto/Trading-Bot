# trail_sl.py
# ============================
# TRAIL SL MODULE (Option A — One-Time Activation)
# ============================

import logging
from typing import Optional, Set

import config
import session
from config import TRAIL_ACTIVATION_PERC, TRAIL_SL_PERC, API_POSITIONS

logger = logging.getLogger("trail_sl")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [trail_sl] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)

# In-memory set to avoid re-applying the one-time trail to the same position repeatedly.
_applied_trails: Set[str] = set()


def _update_stop_level(deal_id: str, new_sl: float) -> bool:
    """
    Update stopLevel for a single position on Capital.com.
    Uses session.request which applies authentication and retry logic.
    """
    if not deal_id:
        return False

    url = f"{API_POSITIONS}/{deal_id}"
    payload = {"stopLevel": float(new_sl)}

    resp = session.request("PUT", url, json=payload)
    if not resp or getattr(resp, "status_code", 0) >= 400:
        logger.warning("Failed to update SL for %s → %s", deal_id, getattr(resp, "status_code", "no_response"))
        return False

    logger.info("Updated SL for %s to %s", deal_id, new_sl)
    return True


def run_trailing_sl() -> None:
    """
    One-time trailing stop:
      - Activates when profit reaches >= TRAIL_ACTIVATION_PERC (e.g. 0.50 for 50%)
      - Moves SL to entry + profit * TRAIL_SL_PERC (for longs)
        or entry - profit * TRAIL_SL_PERC (for shorts)
      - Only moves once per position (tracked in-memory)
    """
    try:
        positions = session.get_positions() or []
        enriched = session.enrich_positions(positions) or []
    except Exception:
        logger.exception("Failed to fetch/enrich positions for trailing SL")
        return

    for p in enriched:
        # deal id may be under 'dealId' or 'id'
        deal_id = p.get("dealId") or p.get("id")
        if not deal_id:
            continue
        deal_id = str(deal_id)

        # Skip if we've already applied a one-time trail for this deal in this process
        if deal_id in _applied_trails:
            continue

        # direction normalized by session.enrich_positions is "Long"/"Short"
        direction = (p.get("direction") or "").strip()
        entry_price = p.get("price")
        current_price = p.get("current_price")
        existing_sl = p.get("stopLevel") or p.get("stop_level") or p.get("sl")

        # Validate numeric values
        try:
            entry_price_f = float(entry_price)
            current_price_f = float(current_price)
        except Exception:
            logger.debug("Skipping %s: invalid numeric entry/current price (entry=%s current=%s)", deal_id, entry_price, current_price)
            continue

        # Compute profit depending on side
        dir_lower = direction.lower() if isinstance(direction, str) else ""
        if dir_lower in ("long", "buy"):
            profit = current_price_f - entry_price_f
        else:
            profit = entry_price_f - current_price_f

        if profit <= 0:
            # no unrealized profit
            continue

        profit_perc = profit / entry_price_f if entry_price_f != 0 else 0.0

        # Activation threshold
        if profit_perc < float(getattr(config, "TRAIL_ACTIVATION_PERC", TRAIL_ACTIVATION_PERC)):
            continue

        # Compute one-time trail stop
        trail_sl = None
        try:
            if dir_lower in ("long", "buy"):
                trail_sl = entry_price_f + profit * float(getattr(config, "TRAIL_SL_PERC", TRAIL_SL_PERC))
            else:
                trail_sl = entry_price_f - profit * float(getattr(config, "TRAIL_SL_PERC", TRAIL_SL_PERC))
            trail_sl = round(float(trail_sl), 2)
        except Exception:
            logger.exception("Failed to compute trail SL for %s", deal_id)
            continue

        # If existing SL is present, ensure we only move it in the protective direction
        try:
            existing_sl_f = float(existing_sl) if existing_sl is not None else None
        except Exception:
            existing_sl_f = None

        # For longs: only move if trail_sl is greater than existing SL (i.e., tighten upwards)
        # For shorts: only move if trail_sl is less than existing SL (i.e., tighten downwards)
        should_update = False
        if existing_sl_f is None:
            should_update = True
        else:
            if dir_lower in ("long", "buy") and trail_sl > existing_sl_f:
                should_update = True
            if dir_lower in ("short", "sell") and trail_sl < existing_sl_f:
                should_update = True

        if not should_update:
            logger.debug("Trail SL not beneficial for %s: existing_sl=%s computed=%s", deal_id, existing_sl_f, trail_sl)
            # mark as applied to avoid repeated checks if desired; do not mark if you want re-evaluation later
            _applied_trails.add(deal_id)
            continue

        # Apply the new stop level
        if _update_stop_level(deal_id, trail_sl):
            logger.info("One-time trail applied → deal=%s dir=%s entry=%s current=%s newSL=%s", deal_id, direction, entry_price_f, current_price_f, trail_sl)
            _applied_trails.add(deal_id)
        else:
            logger.warning("Failed to apply trail SL for deal %s", deal_id)
