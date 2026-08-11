# ============================
# TradingView Alert Parser (Updated)
# ============================

def parse_tradingview_alert(data):
    """
    Accepts:
    - RAW string alerts
    - JSON alerts
    """
    if isinstance(data, str):
        return parse_raw_alert(data)

    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ---------------------------------------------------------
# JSON ALERT PARSER
# ---------------------------------------------------------

def parse_json_alert(data):
    """
    JSON TradingView alert format:
    {
        "symbol": "NVDA",
        "action": "buy",
        "quantity": 1,
        "payload": "BUY|NVDA|SL:123|TP:130"
    }
    """

    symbol = normalise_symbol(data.get("symbol"))
    if not symbol:
        raise ValueError("Missing symbol")

    payload_raw = data.get("payload")
    sl = None
    tp = None

    if payload_raw:
        payload = parse_payload(payload_raw)
        sl = payload["sl"]
        tp = payload["tp"]
        direction = payload["direction"]
    else:
        direction = data.get("action", "").lower()

    # BUY requires SL/TP
    if direction == "buy" and (sl is None or tp is None):
        raise ValueError("Missing SL/TP for BUY")

    # SELL / CLOSE do NOT require SL/TP

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": safe_float(data.get("quantity", 1)),
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# RAW ALERT PARSER
# ---------------------------------------------------------

def parse_raw_alert(raw: str):
    parts = raw.split("|")

    if len(parts) < 2:
        raise ValueError("Invalid raw alert format")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])
    quantity = 1

    sl = None
    tp = None

    for part in parts:
        part = part.strip()

        if part.upper().startswith("SL:"):
            sl = safe_float(part.replace("SL:", "").strip())

        if part.upper().startswith("TP:"):
            tp = safe_float(part.replace("TP:", "").strip())

    # BUY requires SL/TP
    if direction == "buy" and (sl is None or tp is None):
        raise ValueError("Missing SL/TP for BUY")

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# PAYLOAD PARSER
# ---------------------------------------------------------

def parse_payload(payload: str):
    parts = payload.split("|")

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

    # BUY requires SL/TP
    if direction == "buy" and (sl is None or tp is None):
        raise ValueError("Missing SL/TP for BUY")

    return {
        "direction": direction,
        "symbol": symbol,
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def normalise_symbol(symbol: str):
    if not symbol:
        return None
    return symbol.strip().upper()


def safe_float(value):
    try:
        return float(value)
    except:
        raise ValueError("Invalid numeric value")
