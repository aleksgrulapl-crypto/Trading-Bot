import datetime
from decimal import Decimal

# ---------------------------------------------------------
# TIMESTAMP
# ---------------------------------------------------------

def timestamp():
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------
# SAFE FLOAT + ROUNDING
# ---------------------------------------------------------

def safe_float(x):
    try:
        return float(Decimal(str(x)))
    except:
        return 0.0

def round2(x):
    try:
        return round(float(Decimal(str(x))), 2)
    except:
        return 0.0


# ---------------------------------------------------------
# AVAILABLE BALANCE COLOR CODING
# ---------------------------------------------------------

def available_color(available, deposit):
    if deposit == 0:
        return "gray"

    ratio = available / deposit

    if ratio >= 0.40:
        return "lime"
    elif ratio >= 0.20:
        return "yellow"
    else:
        return "red"


# ---------------------------------------------------------
# MARGIN PRESSURE WARNINGS
# ---------------------------------------------------------

def margin_warning(available, deposit):
    if deposit == 0:
        return None

    ratio = available / deposit

    if ratio < 0.20:
        return "⚠️ Margin Danger: Available funds critically low."
    elif ratio < 0.40:
        return "⚠️ Margin Warning: Available funds dropping."
    else:
        return None


# ---------------------------------------------------------
# TICKER RESOLUTION
# ---------------------------------------------------------

def resolve_ticker(epic):
    if not epic:
        return None
    if "." in epic:
        return epic.split(".")[0]
    return epic


# ---------------------------------------------------------
# PROFIT/LOSS CALCULATION
# ---------------------------------------------------------

def calculate_profit_loss(direction, open_price, current_price, size):
    if not direction or open_price is None or current_price is None:
        return 0.0

    open_price = safe_float(open_price)
    current_price = safe_float(current_price)
    size = safe_float(size)

    if direction.upper() == "BUY":
        return (current_price - open_price) * size

    if direction.upper() == "SELL":
        return (open_price - current_price) * size

    return 0.0


# ---------------------------------------------------------
# POSITION PARSING
# ---------------------------------------------------------

def parse_positions(raw_positions):
    parsed = []

    for p in raw_positions:
        market = p.get("market", {})
        pos = p.get("position", {})

        parsed.append({
            "id": pos.get("dealId"),
            "epic": market.get("epic"),
            "ticker": market.get("symbol"),
            "direction": pos.get("direction"),
            "size": safe_float(pos.get("size")),
            "price": safe_float(pos.get("level")),
            "current_price": safe_float(market.get("bid")),
            "profit": safe_float(pos.get("upl")),
            "profitLoss": None,
            "instrument": market
        })

    return parsed


# ---------------------------------------------------------
# ACCOUNT PARSING (YOUR CUSTOM LOGIC)
# ---------------------------------------------------------

def parse_account(raw):
    accounts = raw.get("accounts", [])
    if not accounts:
        return {"balance": None, "equity": None, "margin": None}

    acc = accounts[0]
    bal = acc.get("balance", {})

    deposit = safe_float(bal.get("deposit"))
    balance = safe_float(bal.get("balance"))
    available = safe_float(bal.get("available"))
    profit_loss = safe_float(bal.get("profitLoss"))

    ui_balance = deposit
    ui_equity = balance
    ui_margin = deposit - available + profit_loss

    return {
        "balance": round2(ui_balance),
        "equity": round2(ui_equity),
        "margin": round2(ui_margin),
        "available": round2(available),
        "available_color": available_color(available, deposit),
        "margin_warning": margin_warning(available, deposit)
    }

# ---------------------------------------------------------
# EPIC MAPPING (Capital.com symbols)
# ---------------------------------------------------------

EPIC_MAP = {
    # US Stocks
    "AAPL": "US.AAPL",
    "TSLA": "US.TSLA",
    "MSFT": "US.MSFT",
    "NVDA": "US.NVDA",
    "MU": "US.MU",
    "AMD": "US.AMD",
    "META": "US.META",
    "GOOGL": "US.GOOGL",
    "AMZN": "US.AMZN",

    # FX (examples)
    "EURUSD": "CS.D.EURUSD.MINI.IP",
    "GBPUSD": "CS.D.GBPUSD.MINI.IP",
    "USDJPY": "CS.D.USDJPY.MINI.IP",

    # Indices
    "SPX": "IX.D.SPTRD.IP",
    "NAS100": "IX.D.NASDAQ.100.IP",

    # Crypto (Capital.com CFD symbols)
    "BTCUSD": "CRYPTO.BTCUSD",
    "ETHUSD": "CRYPTO.ETHUSD"
}

def map_symbol_to_epic(symbol: str):
    if not symbol:
        return None
    symbol = symbol.upper()
    return EPIC_MAP.get(symbol)

