import datetime

# ---------------------------------------------------------
# TIMESTAMP
# ---------------------------------------------------------

def timestamp():
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------
# SAFE FLOAT
# ---------------------------------------------------------

def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0


# ---------------------------------------------------------
# TICKER RESOLUTION
# ---------------------------------------------------------

def resolve_ticker(epic):
    """
    Convert Capital.com EPIC into a human-friendly ticker.
    Example: 'AAPL.US' -> 'AAPL'
    """
    if not epic:
        return None

    if "." in epic:
        return epic.split(".")[0]

    return epic


# ---------------------------------------------------------
# PROFIT/LOSS CALCULATION
# ---------------------------------------------------------

def calculate_profit_loss(direction, open_price, current_price, size):
    """
    Calculate PnL for CFD positions.
    """
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
# POSITION PARSING (RAW API → CLEAN STRUCTURE)
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
            "price": safe_float(pos.get("level")),  # open price
            "current_price": safe_float(market.get("bid")),  # live price
            "profit": safe_float(pos.get("upl")),  # unrealised P/L
            "profitLoss": None,  # enriched later
            "instrument": market  # enriched later
        })

    return parsed



# ---------------------------------------------------------
# ACCOUNT PARSING
# ---------------------------------------------------------

def parse_account(raw):
    """
    Extract balance, equity, margin from Capital.com account response.
    """
    return {
        "balance": raw.get("balance"),
        "equity": raw.get("equity"),
        "margin": raw.get("margin")
    }
