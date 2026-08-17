# ============================
# TRAIL SL MODULE (Option A — One-Time Activation)
# ============================

from config import TRAIL_ACTIVATION_PERC, TRAIL_SL_PERC, API_POSITIONS
import session


def _update_stop_level(deal_id, new_sl):
    """
    Update stopLevel for a single position on Capital.com.
    """
    url = f"{API_POSITIONS}/{deal_id}"
    payload = {
        "stopLevel": new_sl
    }

    resp = session.request("PUT", url, json=payload)
    if not resp or resp.status_code >= 400:
        print(f"[TrailSL] Failed to update SL for {deal_id} → {resp.status_code if resp else 'no response'}", flush=True)
        return False

    print(f"[TrailSL] Updated SL for {deal_id} to {new_sl}", flush=True)
    return True


def run_trailing_sl():
    """
    One-time trailing stop:
    - Activates when profit reaches +TRAIL_ACTIVATION_PERC (e.g. 50%)
    - Moves SL to entry + profit * TRAIL_SL_PERC (e.g. 30%)
    - Only moves once per position (no dynamic trailing).
    """
    positions = session.get_positions() or []
    enriched = session.enrich_positions(positions)

    for p in enriched:
        deal_id = p.get("position_id")
        direction = p.get("side")
        entry_price = p.get("entry_price")
        current_price = p.get("current_price")
        sl = p.get("sl")

        if not deal_id or entry_price is None or current_price is None:
            continue

        # Unrealized profit in price terms
        if direction == "BUY":
            profit = current_price - entry_price
        else:
            profit = entry_price - current_price

        if profit <= 0:
            continue

        profit_perc = profit / entry_price

        if profit_perc < TRAIL_ACTIVATION_PERC:
            continue

        # One-time trailing SL level
        trail_sl = entry_price + profit * TRAIL_SL_PERC if direction == "BUY" else entry_price - profit * TRAIL_SL_PERC

        # Only move SL if new SL is tighter than old one (for BUY: higher; for SELL: lower)
        if sl is not None:
            if direction == "BUY" and trail_sl <= sl:
                continue
            if direction == "SELL" and trail_sl >= sl:
                continue

        trail_sl = round(trail_sl, 2)

        if _update_stop_level(deal_id, trail_sl):
            print(f"[TrailSL] One-time trail applied → {deal_id} dir={direction} entry={entry_price} current={current_price} newSL={trail_sl}", flush=True)
