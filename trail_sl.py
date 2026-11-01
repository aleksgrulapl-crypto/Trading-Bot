# trail_sl.py
# ============================
# TRAIL SL MODULE (Continuous Trailing)
# ============================

import logging
import time
from typing import Dict, Optional

import config
import session
import trail_settings
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
    Tighten the trailing stop as profit extends further beyond activation, so
    the SL hugs price more closely the deeper a trade goes in-profit. The
    tightening is uncapped in how many "steps" it can take (previously capped
    at 3x the activation threshold) and ramps faster per step, reaching the
    TRAIL_MAX_PERC ceiling sooner than before.
    """
    try:
        profit_perc_f = float(profit_perc)
        activation_perc_f = float(activation_perc)
        base_trail_perc_f = float(base_trail_perc)
    except Exception:
        return float(base_trail_perc)

    if activation_perc_f <= 0:
        return base_trail_perc_f

    max_trail_perc = max(base_trail_perc_f, _normalize_percent(getattr(config, "TRAIL_MAX_PERC", 0.95), 0.95))

    profit_multiple = profit_perc_f / activation_perc_f
    tighten_steps = max(profit_multiple - 1.0, 0.0)
    tighten_step_perc = _normalize_percent(getattr(config, "TRAIL_TIGHTEN_STEP_PERC", 0.15), 0.15)
    return min(max_trail_perc, base_trail_perc_f + (tighten_step_perc * tighten_steps))


def _profit_in_gbp(profit: Optional[float], position: dict) -> Optional[float]:
    """Convert a position's monetary unrealized P&L (as reported by the
    broker, typically in the instrument's quote currency) into GBP, using
    the same FX_USD_GBP rate the dashboard/trade log use elsewhere. If the
    position is already denominated in GBP, no conversion is applied."""
    if profit is None:
        return None
    try:
        profit_f = float(profit)
    except Exception:
        return None

    currency = str((position or {}).get("currency") or "USD").upper()
    if currency == "GBP":
        return profit_f

    fx = float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)
    return profit_f * fx


def _extract_tp_level(position: dict, side: str) -> Optional[float]:
    candidates = (
        position.get("profitLevel"),
        position.get("limitLevel"),
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
        getattr(config, "TRAIL_ACTIVATION_TP_FRACTION", 0.5),
        0.5,
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

    # Whichever threshold is reached sooner activates the trail.
    tp_activation = tp_move_perc * activation_tp_fraction
    if default_activation <= 0:
        return tp_activation
    return min(default_activation, tp_activation)


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


_sent_state: Dict[str, Dict[str, float]] = {}
_tp_fail_ts: Dict[str, float] = {}


def _remove_take_profit(deal_id: str) -> bool:
    """Drop the TP on a position and remember that its SL now trails profit."""
    cooldown = _cfg_float("TRAIL_FAILURE_COOLDOWN_SECONDS", 120.0)
    last_fail = _tp_fail_ts.get(deal_id, 0.0)
    if last_fail and time.monotonic() - last_fail < cooldown:
        return False

    resp = session.request("PUT", f"{API_POSITIONS}/{deal_id}", json={"profitLevel": None})
    if not resp or getattr(resp, "status_code", 0) >= 400:
        _tp_fail_ts[deal_id] = time.monotonic()
        logger.warning(
            "Failed to remove TP for %s → %s body=%s",
            deal_id,
            getattr(resp, "status_code", "no_response"),
            (getattr(resp, "text", "") or "")[:300],
        )
        return False

    _tp_fail_ts.pop(deal_id, None)
    trail_settings.mark_tp_trail(deal_id)
    logger.info("TP removed for %s; SL now trails profit", deal_id)
    return True


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(getattr(config, name, default))
    except Exception:
        return default


def _record_sent(deal_id: str, sl: float) -> None:
    _sent_state[deal_id] = {"sl": float(sl), "ts": time.monotonic(), "fail_ts": 0.0}


def _record_failure(deal_id: str) -> None:
    st = _sent_state.setdefault(deal_id, {"sl": None, "ts": 0.0, "fail_ts": 0.0})
    st["fail_ts"] = time.monotonic()


def _throttle_allows(deal_id: str, side: str, new_sl: float,
                     existing_sl: Optional[float], entry: float) -> bool:
    """Avoid spamming the broker with PUT /positions modifications: skip
    tiny SL nudges, rate-limit per deal, and back off after failures.
    The first move above entry-protective territory is never delayed."""
    st = _sent_state.get(deal_id)
    now = time.monotonic()

    if st:
        cooldown = _cfg_float("TRAIL_FAILURE_COOLDOWN_SECONDS", 120.0)
        if st.get("fail_ts") and now - st["fail_ts"] < cooldown:
            logger.debug("Trail SL for %s in failure cooldown", deal_id)
            return False

    # Reference = best SL we know of (broker value may be stale after a send).
    ref = existing_sl
    last_sl = st.get("sl") if st else None
    if last_sl is not None:
        if ref is None:
            ref = last_sl
        else:
            ref = max(ref, last_sl) if side == "long" else min(ref, last_sl)

    if ref is None:
        return True

    improvement = (new_sl - ref) if side == "long" else (ref - new_sl)
    if improvement <= 0:
        return False

    # Still sitting at the initial (loss-side) stop: lock in profit at once.
    first_lock = (ref < entry) if side == "long" else (ref > entry)
    if first_lock:
        return True

    min_step = _cfg_float("TRAIL_MIN_STEP_PERC", 0.001) * entry
    if improvement < min_step:
        logger.debug("Trail SL step %.6f below min step %.6f for %s", improvement, min_step, deal_id)
        return False

    min_interval = _cfg_float("TRAIL_MIN_UPDATE_INTERVAL_SECONDS", 60.0)
    if st and st.get("ts") and now - st["ts"] < min_interval:
        return False
    return True


def run_trailing_sl() -> None:
    """
    Trailing stops:
      - TP trail (every trade): at TP_TRAIL_TRIGGER_FRACTION of the way to the
        TP, lock TP_TRAIL_LOCK_FRACTION of the profit in the SL, remove the TP,
        then keep ratcheting the SL at that fraction of the profit.
      - SL trail (only trades switched ON, i.e. Trader trades; OFF by default):
        activates when profit reaches the Trader-chosen GBP level, or by
        default >= TRAIL_ACTIVATION_PERC / TRAIL_ACTIVATION_PNL_GBP / the TP
        fraction, whichever comes first.
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

    settings = trail_settings.all_settings()

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

        rec = settings.get(deal_id) or {}
        tp_level = _extract_tp_level(p, side)
        tp_active = bool(rec.get("tp_trail"))
        tp_trigger = False
        if not tp_active and tp_level is not None and entry_price_f > 0:
            tp_move = abs(tp_level - entry_price_f)
            trigger = _normalize_percent(getattr(config, "TP_TRAIL_TRIGGER_FRACTION", 0.90), 0.90)
            tp_trigger = tp_move > 0 and trigger > 0 and (profit / tp_move) >= trigger
        tp_mode = tp_active or tp_trigger
        std_mode = bool(rec.get("enabled"))
        if not (tp_mode or std_mode):
            continue

        candidates = []
        try:
            if tp_mode:
                lock = _normalize_percent(getattr(config, "TP_TRAIL_LOCK_FRACTION", 0.80), 0.80)
                candidates.append(entry_price_f + profit * lock if side == "long" else entry_price_f - profit * lock)

            if std_mode:
                # Trader opt-in trail. A Trader-chosen GBP level replaces the
                # default thresholds; otherwise EITHER the percentage OR the
                # flat GBP floor activates it, whichever comes first.
                pnl_gbp = _profit_in_gbp(p.get("profit", p.get("upl")), p)
                custom_gbp = rec.get("activation_gbp")
                try:
                    custom_gbp = float(custom_gbp) if custom_gbp is not None else None
                except (TypeError, ValueError):
                    custom_gbp = None
                if custom_gbp is not None and custom_gbp > 0:
                    active = pnl_gbp is not None and pnl_gbp >= custom_gbp
                    activation_perc = profit_perc * (custom_gbp / pnl_gbp) if active and pnl_gbp > 0 else 0.0
                else:
                    activation_perc = _activation_threshold_perc(p, side, entry_price_f)
                    pnl_floor_gbp = float(getattr(config, "TRAIL_ACTIVATION_PNL_GBP", 15.0) or 0.0)
                    meets_perc = profit_perc >= activation_perc
                    meets_pnl_floor = pnl_gbp is not None and pnl_floor_gbp > 0 and pnl_gbp >= pnl_floor_gbp
                    active = meets_perc or meets_pnl_floor
                if active:
                    trail_perc = _normalize_percent(getattr(config, "TRAIL_SL_PERC", TRAIL_SL_PERC), TRAIL_SL_PERC)
                    trail_perc = _effective_trail_percent(profit_perc, activation_perc, trail_perc)
                    candidates.append(entry_price_f + profit * trail_perc if side == "long" else entry_price_f - profit * trail_perc)

            if not candidates:
                continue
            trail_sl = max(candidates) if side == "long" else min(candidates)
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
            sl_secured = existing_sl_f is not None
        elif not _throttle_allows(deal_id, side, trail_sl, existing_sl_f, entry_price_f):
            sl_secured = False
        elif _update_stop_level(deal_id, trail_sl):
            _record_sent(deal_id, trail_sl)
            sl_secured = True
            logger.info("Trail SL updated → deal=%s dir=%s entry=%s current=%s newSL=%s", deal_id, direction, entry_price_f, current_price_f, trail_sl)
        else:
            _record_failure(deal_id)
            sl_secured = False
            logger.warning("Failed to apply trail SL for deal %s", deal_id)

        # TP trail: only drop the TP once the SL is already locking profit.
        if tp_trigger and sl_secured:
            _remove_take_profit(deal_id)
