"""Stock universe grouped by sector, with cached events/sentiment for the
dashboard's Stocks page."""

from typing import Any, Dict, Iterable, List

import config
import events

SECTORS: Dict[str, List[str]] = {
    "Semiconductors": ["NVDA", "AMD", "INTC", "MU", "AVGO", "MRVL", "AMAT", "LRCX", "WDC", "STX"],
    "Technology": ["MSFT", "AAPL", "PLTR", "CRM", "ORCL", "CSCO", "SHOP", "QBTS", "NBIS", "LITE"],
    "Communication & Media": ["META", "GOOGL", "GOOG", "NFLX"],
    "Consumer & Retail": ["TSLA", "AMZN", "BABA", "WMT"],
    "Healthcare": ["UNH", "LLY", "ISRG"],
    "Energy & Clean Tech": ["PLUG", "BE"],
    "Financials & Crypto": ["COIN"],
    "Materials": ["GOLD"],
    "Industrials & Space": ["SPCX"],
}
OTHER = "Other"
_SECTOR_OF = {sym: sector for sector, syms in SECTORS.items() for sym in syms}


def sector_of(symbol: str) -> str:
    return _SECTOR_OF.get(symbol, OTHER)


def universe(extra: Iterable[Any] = ()) -> List[str]:
    """Every stock the bot is configured for, plus any extra tickers (e.g.
    from the trade log), as normalised Finnhub symbols."""
    raw = list(getattr(config, "EPIC_MAP", {}) or {}) + list(getattr(config, "TICKER_SETTINGS", {}) or {}) + list(extra)
    return sorted({events.normalize_symbol(t) for t in raw} - {""})


def build_sectors(open_trades: List[Dict[str, Any]], extra: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    """Sector groups of stock cards. Open positions sort first within a sector."""
    cache = events._load()
    positions: Dict[str, List[Dict[str, Any]]] = {}
    for t in open_trades or []:
        sym = events.normalize_symbol(t.get("ticker"))
        if sym:
            positions.setdefault(sym, []).append(t)

    symbols = universe(list(extra) + [t.get("ticker") for t in open_trades or []])
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for sym in symbols:
        open_here = positions.get(sym, [])
        sides = sorted({str(t.get("side") or t.get("direction") or "").title() for t in open_here} - {""})
        info = events.event_info(sym, sides[0] if len(sides) == 1 else None, cache)
        conflict = bool(info) and any(
            events.is_conflict(t.get("side") or t.get("direction"), info["sentiment"]) for t in open_here
        )
        groups.setdefault(sector_of(sym), []).append({
            "symbol": sym,
            "info": info,
            "open_count": len(open_here),
            "sides": sides,
            "conflict": conflict,
        })

    ordered = [s for s in list(SECTORS) + [OTHER] if s in groups]
    return [
        {
            "name": name,
            "stocks": sorted(groups[name], key=lambda c: (-c["open_count"], c["symbol"])),
            "open_total": sum(c["open_count"] for c in groups[name]),
        }
        for name in ordered
    ]
