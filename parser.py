# ============================
# TradingView Alert Parser (FINAL)
# ============================

def parse_tradingview_alert(data):
    if isinstance(data, str):
        return parse_raw_alert(data)

    if isinstance(data, dict):
        return parse_json_alert(data)

    raise ValueError("Invalid alert format")


# ---------------------------------------------------------
# R:R = 1:2 TP CALCULATION
# ---------------------------------------------------------

def rr2_tp(entry, sl):
    distance = abs(entry - sl)
    return entry + (distance * 2)


# ---------------------------------------------------------
# JSON ALERT PARSER
# ---------------------------------------------------------

def parse_json_alert(data):
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

    quantity = safe_float(data.get("quantity", 1))

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# RAW ALERT PARSER
# ---------------------------------------------------------

def parse_raw_alert(raw: str):
    parts = raw.split("|")

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
