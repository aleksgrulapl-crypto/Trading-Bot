from datetime import datetime
import pytz

UK_TZ = pytz.timezone("Europe/London")

def now_uk():
    return datetime.now(UK_TZ)

def today_date_str():
    return now_uk().strftime("%Y-%m-%d")

def timestamp():
    return now_uk().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------
# POSITION PARSER (FULL METADATA)
# ---------------------------------------------------------

def parse_position(raw):
    pos = raw.get("position", {})
    market = raw.get("market", {})
    instrument = raw.get("instrument", {})

    epic = pos.get("epic") or instrument.get("epic")
    ticker = instrument.get("symbol") or epic

    profitLoss = raw.get("profitLoss") or instrument.get("profitLoss") or 0

    return {
        "id": pos.get("dealId"),
        "ticker": ticker,
        "size": pos.get("size"),
        "price": pos.get("level"),
        "current_price": market.get("offer") or market.get("bid"),
        "profit": float(profitLoss),
        "direction": pos.get("direction")
    }

def parse_positions(raw_positions):
    return [parse_position(p) for p in raw_positions]

# ---------------------------------------------------------
# ACCOUNT PARSER (CFD ONLY)
# ---------------------------------------------------------

def parse_account(raw):
    return {
        "balance": raw.get("balance"),
        "equity": raw.get("equity"),
        "margin": raw.get("margin")
    }

def fmt_money(value):
    try:
        return f"£{round(float(value), 2)}"
    except:
        return "£0.00"
