"""Upcoming events (earnings) and news sentiment for currently open trades.

Data comes from Finnhub's free tier and is only fetched for tickers with an
open trade. Results are cached on disk; the dashboard only ever reads the
cache, so page loads never wait on the network.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger("events")

FINNHUB_URL = "https://finnhub.io/api/v1"
HTTP_TIMEOUT = 10
NEWS_LOOKBACK_DAYS = 3
MAX_HEADLINES = 5
# Net score (bullish - bearish keyword hits) needed to leave Neutral.
SENTIMENT_THRESHOLD = 2

BULLISH_WORDS = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies",
    "upgrade", "upgrades", "upgraded", "record", "growth", "raises", "raised", "outperform",
    "bullish", "strong", "gain", "gains", "profit", "wins", "approval", "approved", "buyback",
}
BEARISH_WORDS = {
    "miss", "misses", "missed", "plunge", "plunges", "fall", "falls", "drop", "drops", "slump",
    "downgrade", "downgrades", "downgraded", "lawsuit", "probe", "investigation", "recall",
    "cuts", "cut", "warning", "warns", "bearish", "weak", "loss", "losses", "layoffs", "fraud",
    "ban", "bans", "tariff", "tariffs", "decline", "declines", "underperform",
}

_lock = threading.Lock()


def _enabled() -> bool:
    return bool(getattr(config, "FINNHUB_API_KEY", ""))


def _path() -> str:
    return getattr(config, "EVENTS_CACHE_PATH", "/data/events_cache.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    path = _path()
    try:
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        logger.exception("events: failed to persist cache")


def normalize_symbol(ticker: Any) -> str:
    """Broker epics may carry suffixes (e.g. 'NVDA.US'); Finnhub wants 'NVDA'."""
    return re.split(r"[.\s/:]", str(ticker or "").strip().upper())[0]


def score_headlines(headlines: List[str]) -> Dict[str, Any]:
    bull = bear = 0
    for h in headlines:
        words = set(re.findall(r"[a-z']+", h.lower()))
        bull += len(words & BULLISH_WORDS)
        bear += len(words & BEARISH_WORDS)
    net = bull - bear
    label = "Bullish" if net >= SENTIMENT_THRESHOLD else "Bearish" if net <= -SENTIMENT_THRESHOLD else "Neutral"
    return {"sentiment": label, "score": net}


_last_call = 0.0
_throttle_lock = threading.Lock()
# Finnhub free tier allows 60 calls/min; stay safely under it.
MIN_CALL_INTERVAL = 1.1


def _get(endpoint: str, params: Dict[str, Any]) -> Any:
    global _last_call
    with _throttle_lock:
        wait = MIN_CALL_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
    params = dict(params, token=config.FINNHUB_API_KEY)
    resp = requests.get(f"{FINNHUB_URL}/{endpoint}", params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_earnings(symbol: str, today: date) -> Optional[str]:
    data = _get("calendar/earnings", {
        "symbol": symbol, "from": today.isoformat(), "to": (today + timedelta(days=120)).isoformat(),
    })
    dates = sorted(
        e["date"] for e in (data or {}).get("earningsCalendar", []) if e.get("date") and e["date"] >= today.isoformat()
    )
    return dates[0] if dates else None


def _fetch_news(symbol: str, today: date) -> Dict[str, Any]:
    items = _get("company-news", {
        "symbol": symbol, "from": (today - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat(), "to": today.isoformat(),
    }) or []
    items = sorted(items, key=lambda i: i.get("datetime", 0), reverse=True)
    headlines = [str(i.get("headline") or "") for i in items if i.get("headline")]
    result = score_headlines(headlines)
    result["headlines"] = headlines[:MAX_HEADLINES]
    return result


def refresh_symbol(symbol: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Refresh stale cache parts for one symbol. Never raises."""
    now = now if now is not None else time.time()
    today = datetime.fromtimestamp(now).date()
    with _lock:
        entry = dict(_load().get(symbol) or {})
    try:
        if now - entry.get("earnings_ts", 0) > config.EVENTS_EARNINGS_TTL_HOURS * 3600:
            entry["earnings_date"] = _fetch_earnings(symbol, today)
            entry["earnings_ts"] = now
        if now - entry.get("news_ts", 0) > config.EVENTS_NEWS_TTL_MIN * 60:
            entry.update(_fetch_news(symbol, today))
            entry["news_ts"] = now
    except Exception as exc:
        logger.warning("events: refresh failed for %s: %s", symbol, exc)
        return None
    with _lock:
        data = _load()
        stored = dict(data.get(symbol) or {})
        stored.update(entry)
        data[symbol] = stored
        _save(data)
    return stored


def refresh_open_trades(trades: List[Dict[str, Any]], others: Optional[List[str]] = None) -> int:
    """Refresh events for open-trade tickers first, then any ``others`` (the
    wider stock universe). Calls are throttled to stay within the free tier."""
    if not _enabled():
        return 0
    open_symbols = sorted({normalize_symbol(t.get("ticker")) for t in trades or [] if t.get("ticker")} - {""})
    rest = [s for s in sorted(set(others or [])) if s and s not in open_symbols]
    for symbol in open_symbols + rest:
        refresh_symbol(symbol)
    return len(open_symbols)


def is_conflict(side: Any, sentiment: Optional[str]) -> bool:
    side = str(side or "").lower()
    return (side == "long" and sentiment == "Bearish") or (side == "short" and sentiment == "Bullish")


def event_info(ticker: Any, side: Any = None, cache: Optional[Dict[str, Any]] = None,
               today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Cache-only summary for one trade, or None when nothing is known."""
    entry = (cache if cache is not None else _load()).get(normalize_symbol(ticker))
    if not entry:
        return None
    today = today or date.today()
    days = None
    if entry.get("earnings_date"):
        try:
            days = (date.fromisoformat(entry["earnings_date"]) - today).days
        except ValueError:
            days = None
    if days is not None and days < 0:
        days = None
    sentiment = entry.get("sentiment") or "Neutral"
    return {
        "sentiment": sentiment,
        "score": entry.get("score", 0),
        "headlines": entry.get("headlines") or [],
        "earnings_date": entry.get("earnings_date") if days is not None else None,
        "earnings_days": days,
        "conflict": is_conflict(side, sentiment),
    }


def attach_to_trades(trades: List[Dict[str, Any]]) -> None:
    """Add an ``events`` dict to each open trade (in place, cache-only)."""
    if not trades:
        return
    cache = _load()
    if not cache:
        return
    for t in trades:
        info = event_info(t.get("ticker"), t.get("side") or t.get("direction"), cache)
        if info:
            t["events"] = info


def check_alerts(trades: List[Dict[str, Any]]) -> int:
    """Email the Owner about upcoming earnings and sentiment conflicts on open
    trades, once per ticker/event. Returns the number of alerts queued."""
    import notifications

    if not trades or not notifications.category_enabled("sentiment"):
        return 0
    cache = _load()
    alerted = dict(cache.get("_alerted") or {})
    queued = 0
    today = date.today()
    for t in trades:
        ticker, side = t.get("ticker"), str(t.get("side") or t.get("direction") or "")
        info = event_info(ticker, side, cache, today)
        if not info:
            continue
        symbol = normalize_symbol(ticker)
        if info["earnings_days"] is not None and info["earnings_days"] <= config.EVENTS_ALERT_DAYS:
            key = f"earnings:{symbol}:{info['earnings_date']}"
            if key not in alerted:
                alerted[key] = today.isoformat()
                when = "today" if info["earnings_days"] == 0 else f"in {info['earnings_days']} day(s)"
                notifications.send_event_alert(
                    f"Earnings {when}: {symbol} ({side})",
                    f"You hold an open {side} position in {symbol}.\n"
                    f"Earnings are due on {info['earnings_date']} ({when}).",
                )
                queued += 1
        if info["conflict"]:
            key = f"conflict:{symbol}:{side.lower()}:{info['sentiment']}:{today.isoformat()}"
            if key not in alerted:
                alerted[key] = today.isoformat()
                headlines = "\n".join(f"- {h}" for h in info["headlines"])
                notifications.send_event_alert(
                    f"Sentiment against your {side}: {symbol} is {info['sentiment']}",
                    f"Recent news sentiment for {symbol} is {info['sentiment']}, "
                    f"which conflicts with your open {side} position.\n\n{headlines}",
                )
                queued += 1
    if queued:
        cutoff = (today - timedelta(days=30)).isoformat()
        alerted = {k: v for k, v in alerted.items() if v >= cutoff}
        with _lock:
            data = _load()
            data["_alerted"] = alerted
            _save(data)
    return queued
