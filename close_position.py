# ============================
# CLOSE POSITION MODULE (CLEAN + NORMALIZED SIDE)
# ============================

import session
from auth import auth
from trade_log import log_closed_trade, load_raw_log
from utils import timestamp
from config import API_POSITIONS, API_HISTORY_TRANSACTIONS, API_MARKET


def normalize_side(side):
    if not side:
        return None
    s = side.upper()
    if s == "BUY":
        return "Long"
    if s == "SELL":
        return "Short"
    if s == "LONG":
        return "Long"
    if s == "SHORT":
        return "Short"
    return side.capitalize()


def _find_open_trade(deal_id):
    log = load_raw_log()
    for t in reversed(log):
        if t.get("status") == "OPEN" and str(t.get("dealId")) == str(deal_id):
            return t
    return None


def _snapshot_exit(epic, direction, entry_price, size):
    r = session.request("GET", f"{API_MARKET}/{epic}")
    if not r or r.status_code != 200:
        return None, None

    snap = r.json().get("snapshot", {})
    bid = snap.get("bid")
    offer = snap.get("offer")

    if direction == "Long":
        exit_price = bid
        pnl = (exit_price - entry_price) * size
    else:
        exit_price = offer
        pnl = (entry_price - exit_price) * size

    return exit_price, pnl


def _history_exit(deal_id):
    r = session.request("GET", f"{API_HISTORY_TRANSACTIONS}?max=100")
    if not r or r.status_code != 200:
        return None, None

    txs = r.json().get("transactions", [])
    for tx in txs:
        if str(tx.get("dealId")) == str(deal_id):
            exit_price = tx.get("closeLevel") or tx.get("level") or tx.get("price")
            pnl = tx.get("profitAndLoss") or tx.get("pnl")
            return exit_price, pnl

    return None, None


def close_position(deal_id):
    auth.ensure_token()

    r = session.request("POST", f"{API_POSITIONS}/{deal_id}/close")
    if not r or r.status_code != 200:
        print("[CLOSE] Failed")
        return

    open_trade = _find_open_trade(deal_id)
    pos = session.enrich_positions(session.get_positions())

    pos_match = None
    for p in pos:
        if str(p.get("dealId")) == str(deal_id):
            pos_match = p
            break

    ticker = pos_match.get("ticker") if pos_match else open_trade.get("ticker")
    epic = pos_match.get("epic") if pos_match else open_trade.get("epic")
    direction = normalize_side(pos_match.get("direction") if pos_match else open_trade.get("side"))
    size = float(pos_match.get("size") if pos_match else open_trade.get("size"))
    entry_price = float(pos_match.get("price") if pos_match else open_trade.get("entry_price"))

    exit_price, pnl = _history_exit(deal_id)
    if exit_price is None or pnl is None:
        exit_price, pnl = _snapshot_exit(epic, direction, entry_price, size)

    log_closed_trade(
        deal_id=deal_id,
        ticker=ticker,
        epic=epic,
        side=direction,
        size=size,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        time_entered=open_trade.get("time_entered"),
        timeframe