# ============================
# TradingView Alert Parser (FINAL VERSION — FULL COMPATIBILITY)
# ============================

def parse_tradingview_alert(data):
    """
    Supports:
    - Raw alerts: "BUY|MSFT|SL:420.50|TP:425.80"
    - Raw alerts with timeframe: "BUY|MSFT|AutoTrader5M|SL:420|TP:430"
    - JSON alerts: {"symbol":"MSFT","action":"BUY","sl":420,"tp":430}
    """

    # Raw string alert
    if isinstance(data, str):
        return parse_raw_alert(data)

    # JSON dict alert
    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ============================
# RAW ALERT PARSER
# ============================

def parse_raw_alert(raw: str):
    parts = raw.split("|")

    if len(parts) < 2:
        raise ValueError(f"Invalid raw alert format: {raw}")

    # Direction + Symbol always exist
    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    # Optional timeframe (3rd field only if it starts with AutoTrader)
    timeframe = None
    if len(parts) >= 3 and parts[2].startswith("AutoTrader"):
        timeframe = parts[2]

    sl = None
    tp = None

    # Extract SL/TP from any position
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
        "tp": tp,
        "timeframe": timeframe
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
    timeframe = data.get("timeframe")
    quantity = safe_float(data.get("quantity", 1))

    # JSON payload support (legacy)
    payload_raw = data.get("payload")
    if payload_raw:
        payload = parse_payload(payload_raw)
        direction = payload["direction"]
        sl = payload["sl"]
        tp = payload["tp"]
        timeframe = payload["timeframe"]

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp,
        "timeframe": timeframe
    }


# ============================
# PAYLOAD PARSER (JSON alerts)
# ============================

def parse_payload(payload: str):
    parts = payload.split("|")

    if len(parts) < 2:
        raise ValueError(f"Invalid payload format: {payload}")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    timeframe = None
    if len(parts) >= 3 and parts[2].startswith("AutoTrader"):
        timeframe = parts[2]

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
        "tp": tp,
        "timeframe": timeframe
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
