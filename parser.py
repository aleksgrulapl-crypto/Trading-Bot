# ============================
# TradingView Alert Parser (RESTORED + MODERNISED)
# ============================

# ---------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------

def parse_tradingview_alert(data):
    """
    Accepts:
    - RAW string alerts
    - JSON alerts
    """

    # RAW alert
    if isinstance(data, str):
        return parse_raw_alert(data)

    # JSON alert
    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ---------------------------------------------------------
# JSON ALERT PARSER (RESTORED)
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
    if not payload_raw:
        raise ValueError("Missing payload")

    payload = parse_payload(payload_raw)

    action = data.get("action", "").lower() or payload["direction"]
    quantity = safe_float(data.get("quantity", 1))

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "sl": payload["sl"],
        "tp": payload["tp"]
    }


# ---------------------------------------------------------
# RAW ALERT PARSER (RESTORED)
# ---------------------------------------------------------

def parse_raw_alert(raw: str):
    """
    RAW TradingView alert format:
    BUY|NVDA|AutoTrader15M|SL:123.45|TP:130.22|EMA:xxx|ST:xxx
    """

    parts = raw.split("|")

    if len(parts) < 4:
        raise ValueError("Invalid raw alert format")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])
    quantity = 1  # RAW alerts do not include quantity

    sl = None
    tp = None

    for part in parts:
        part = part.strip()

        if part.upper().startswith("SL:"):
            sl = safe_float(part.replace("SL:", "").strip())

        if part.upper().startswith("TP:"):
            tp = safe_float(part.replace("TP:", "").strip())

    if sl is None or tp is None:
        raise ValueError("Missing SL/TP")

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# PAYLOAD PARSER (RESTORED)
# ---------------------------------------------------------

def parse_payload(payload: str):
    """
    Payload format:
    BUY|NVDA|SL:123.45|TP:130.22
    """

    parts = payload.split("|")

    if len(parts) < 4:
        raise ValueError("Invalid payload format")

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

    if sl is None or tp is None:
        raise ValueError("Missing SL/TP")

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
