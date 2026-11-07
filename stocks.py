"""Stock universe grouped by sector, with cached events/sentiment for the
dashboard's Stocks page."""

from typing import Any, Dict, Iterable, List

import config
import events

SECTORS: Dict[str, List[str]] = {
    "Semiconductors": ["NVDA", "AMD", "INTC", "MU", "AVGO", "MRVL", "AMAT", "LRCX", "WDC", "STX"],
    "Technology": ["MSFT", "AAPL", "PLTR", "ORCL", "CSCO", "INTU", "ADBE", "CTSH", "QBTS", "NBIS", "LITE"],
    "Communication & Media": ["META", "GOOG", "NFLX", "CMCSA", "DIS"],
    "Consumer & Retail": ["TSLA", "AMZN", "WMT", "LULU", "NKE"],
    "Healthcare": ["UNH", "LLY", "ISRG", "REGN", "ZTS", "CI", "GEHC"],
    "Energy & Clean Tech": ["PLUG", "BE", "AR"],
    "Financials & Crypto": ["PYPL", "SCHW", "SPGI", "CAN"],
    "Industrials & Space": ["SPCX", "RKLB", "ROP"],
    "Commodities": ["GOLD", "OIL_CRUDE"],
}
OTHER = "Other"
_SECTOR_OF = {sym: sector for sector, syms in SECTORS.items() for sym in syms}


def sector_of(symbol: str) -> str:
    return _SECTOR_OF.get(symbol, OTHER)


def universe(extra: Iterable[Any] = ()) -> List[str]:
    """Every stock in SECTORS, plus any extra tickers (e.g.
    from the trade log), as normalised Finnhub symbols."""
    raw = list(_SECTOR_OF) + list(extra)
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

