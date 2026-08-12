# ============================
# TradingView Alert Parser (FINAL — NO TIMEFRAME)
# ============================

def parse_tradingview_alert(data):
    if isinstance(data, str):
        return parse_raw_alert(data)

    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ============================
# RAW ALERT PARSER
# ============================

def parse_raw_alert(raw: str):
    raw = raw.strip().replace("\n", "").replace("\r", "")

    parts = raw.split("|")

    if len(parts) < 2:
        raise ValueError(f"Invalid raw alert format: {raw}")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    sl = None
    tp = None

    for part in parts:
        part = part.strip()

        if part.upper().startswith("SL:"):
            sl = safe_float(part.replace("SL:", "").strip())

        if part.upper().startswith("TP:"):
            tp = safe_float(part.replace("TP:", "").strip())

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": 1,
        "sl": sl,
        "tp": tp
    }


# ============================
# JSON ALERT PARSER
# ============================

def parse_json_alert(data):
    symbol = normalise_symbol(data.get("symbol"))
    if not symbol:
        raise ValueError("Missing symbol")

    direction = data.get("action", "").lower()
    sl = data.get("sl")
    tp = data.get("tp")
    quantity = safe_float(data.get("quantity", 1))

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ============================
# HELPERS
# ============================

def normalise_symbol(symbol: str):
    if not symbol:
        return None
    return symbol.strip().upper()


def safe_float(value):
    try:
        return float(value)
    except:
        raise ValueError(f"Invalid numeric value: {value}")
