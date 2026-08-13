# ============================
# TradingView Alert Parser (RESTORED + CLEANED)
# ============================

def parse_tradingview_alert(data):
    """
    Accepts:
    - Raw TradingView alert string: "BUY|NVDA|SL:123|TP:130"
    - JSON TradingView alert dict: {"symbol": "NVDA", "action": "buy", "payload": "..."}
    """

    if isinstance(data, str):
        return parse_raw_alert(data)

    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ============================
# JSON ALERT PARSER
# ============================

def parse_json_alert(data):
    symbol = normalise_symbol(data.get("symbol"))
    if not symbol:
        raise ValueError("Missing symbol")

    sl = None
    tp = None

    # JSON payload may contain raw alert string
    payload_raw = data.get("payload")
    if payload_raw:
        payload = parse_payload(payload_raw)
        sl = payload["sl"]
        tp = payload["tp"]
        direction = payload["direction"]
    else:
        direction = data.get("action", "").lower()

    quantity = safe_float(data.get("quantity", 1))

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ============================
# RAW ALERT PARSER
# ============================

def parse_raw_alert(raw: str):
    """
    Expected clean format:
    BUY|NVDA|SL:123|TP:130
    SELL|MSFT|SL:321|TP:300
    """

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
# PAYLOAD PARSER (JSON alerts)
# ============================

def parse_payload(payload: str):
    """
    Expected clean format:
    BUY|NVDA|SL:123|TP:130
    """

    parts = payload.split("|")

    if len(parts) < 2:
        raise ValueError(f"Invalid payload format: {payload}")

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
        "direction": direction,
        "symbol": symbol,
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
