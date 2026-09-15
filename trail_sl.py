# trail_sl.py
# ============================
# TRAIL SL MODULE (Continuous Trailing)
# ============================

import logging
from typing import Optional

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


def _normalize_percent(value: Optional[float], fallback: float) -> float:
    """
    Accept both ratio notation (0.30 == 30%) and whole-percent notation (30 == 30%).
    """
    try:
        raw = float(value)
    except Exception:
        raw = float(fallback)

    if raw < 0:
        raw = float(fallback)

    # Support values expressed as whole percentages (e.g. 50 -> 0.50, 0.5 -> 0.005)
    if raw >= 1.0:
        return raw / 100.0
    return raw


def _normalize_side(direction: Optional[str]) -> Optional[str]:
    if not isinstance(direction, str):
        return None
    d = direction.strip().lower()
    if d in ("long", "buy"):
        return "long"
    if d in ("short", "sell"):
        return "short"
    return None


def _round_stop(stop: float, position: dict) -> float:
    """
    Round stop based on market precision if available; fallback to 4 decimals.
    """
    decimals = 4
    try:
        market = (position.get("raw_market") or {}) if isinstance(position, dict) else {}
        dpf = market.get("decimalPlacesFactor")
        if dpf is not None:
            dpf_i = int(dpf)
            if dpf_i >= 1:
                decimals = len(str(dpf_i)) - 1
    except Exception:
        decimals = 4
    return round(float(stop), decimals)


def _effective_trail_percent(profit_perc: float, activation_perc: float, base_trail_perc: float) -> float:
    """
    Tighten the trailing stop as profit extends further beyond activation.
    """
    try:
        profit_perc_f = float(profit_perc)
        activation_perc_f = float(activation_perc)
        base_trail_perc_f = float(base_trail_perc)
    except Exception:
        return float(base_trail_perc)

    if activation_perc_f <= 0:
        return base_trail_perc_f

    profit_multiple = profit_perc_f / activation_perc_f
    tighten_steps = min(max(profit_multiple - 1.0, 0.0), 3.0)
    max_trail_perc = max(base_trail_perc_f, 0.90)
    return min(max_trail_perc, base_trail_perc_f + (0.10 * tighten_steps))


def _extract_tp_level(position: dict, side: str) -> Optional[float]:
    candidates = (
        position.get("profitLevel"),
        position.get("profit_level"),
        position.get("tp"),
        position.get("takeProfit"),
    )
    for candidate in candidates:
        try:
            value = float(candidate)
            if value <= 0:
                continue
            if side == "long" and value > 0:
                return value
            if side == "short" and value > 0:
                return value
        except Exception:
            continue
    return None


def _activation_threshold_perc(position: dict, side: str, entry_price: float) -> float:
    default_activation = _normalize_percent(
        getattr(config, "TRAIL_ACTIVATION_PERC", TRAIL_ACTIVATION_PERC),
        TRAIL_ACTIVATION_PERC,
    )
    if entry_price <= 0:
        return default_activation

    activation_tp_fraction = _normalize_percent(
        getattr(config, "TRAIL_ACTIVATION_TP_FRACTION", 0.25),
        0.25,
    )
    if activation_tp_fraction <= 0:
        return default_activation

    tp_level = _extract_tp_level(position, side)
    if tp_level is None:
        leverage = float(getattr(config, "LEVERAGE", 1) or 1)
        if leverage <= 0:
            leverage = 1.0
        tp_equity_perc = _normalize_percent(getattr(config, "FIXED_TP_PERC", 0.40), 0.40)
        tp_move_perc = tp_equity_perc / leverage
    else:
        tp_move_perc = abs(tp_level - entry_price) / entry_price

    if tp_move_perc <= 0:
        return default_activation

    return max(default_activation, tp_move_perc * activation_tp_fraction)


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
        logger.warning(
            "Failed to update SL for %s → %s body=%s",
            deal_id,
            getattr(resp, "status_code", "no_response"),
            (getattr(resp, "text", "") or "")[:300],
        )
        return False

    logger.info("Updated SL for %s to %s", deal_id, new_sl)
    return True


def run_trailing_sl() -> None:
    """
    Continuous trailing stop:
      - Activates when profit reaches >= TRAIL_ACTIVATION_PERC (e.g. 0.50 for 50%)
      - Moves SL to entry + profit * TRAIL_SL_PERC (for longs)
        or entry - profit * TRAIL_SL_PERC (for shorts)
      - Re-evaluated on every scheduler tick so the SL keeps rising as price rises
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

        direction = (p.get("direction") or "").strip()
        side = _normalize_side(direction)
        if side is None:
            logger.debug("Skipping %s: unsupported direction '%s'", deal_id, direction)
            continue

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
        if side == "long":
            profit = current_price_f - entry_price_f
        else:
            profit = entry_price_f - current_price_f

        if profit <= 0:
            # no unrealized profit
            continue

        profit_perc = profit / entry_price_f if entry_price_f != 0 else 0.0

        # Activation threshold
        activation_perc = _activation_threshold_perc(p, side, entry_price_f)
        if profit_perc < activation_perc:
            continue

        # Compute trail stop
        trail_sl = None
        try:
            trail_perc = _normalize_percent(getattr(config, "TRAIL_SL_PERC", TRAIL_SL_PERC), TRAIL_SL_PERC)
            trail_perc = _effective_trail_percent(profit_perc, activation_perc, trail_perc)
            if side == "long":
                trail_sl = entry_price_f + profit * trail_perc
            else:
                trail_sl = entry_price_f - profit * trail_perc
            trail_sl = _round_stop(float(trail_sl), p)
        except Exception:
            logger.exception("Failed to compute trail SL for %s", deal_id)
            continue

        # Ensure stop remains protective relative to current price
        if side == "long" and trail_sl >= current_price_f:
            logger.debug("Skipping %s: computed long SL %.6f not below current %.6f", deal_id, trail_sl, current_price_f)
            continue
        if side == "short" and trail_sl <= current_price_f:
            logger.debug("Skipping %s: computed short SL %.6f not above current %.6f", deal_id, trail_sl, current_price_f)
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
            if side == "long" and trail_sl > existing_sl_f:
                should_update = True
            if side == "short" and trail_sl < existing_sl_f:
                should_update = True

        if not should_update:
            logger.debug("Trail SL not beneficial for %s: existing_sl=%s computed=%s", deal_id, existing_sl_f, trail_sl)
            continue

        # Apply the new stop level
        if _update_stop_level(deal_id, trail_sl):
            logger.info("Trail SL updated → deal=%s dir=%s entry=%s current=%s newSL=%s", deal_id, direction, entry_price_f, current_price_f, trail_sl)
        else:
            logger.warning("Failed to apply trail SL for deal %s", deal_id)
