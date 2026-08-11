# ============================
# TradingView Alert Parser (FULLY TOLERANT VERSION)
# ============================

import session

# ---------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------

def parse_tradingview_alert(data):
    """
    Accepts:
    - RAW string alerts
    - JSON alerts
    - Alerts with extra fields
    - Alerts with lowercase sl/tp
    - Alerts missing quantity
    - Alerts missing action (infers from payload)
    """

    session.update_last_webhook()

    try:
        # RAW alert (contains |)
        if isinstance(data, str):
            return tolerant_parse_raw(data)

        # JSON alert
        if isinstance(data, dict):
            return tolerant_parse_json(data)

        raise ValueError("Unsupported alert format")

    except Exception as e:
        print(f"[PARSER ERROR] {e}")
        raise ValueError("Invalid alert")


# ---------------------------------------------------------
# TOLERANT JSON PARSER
# ---------------------------------------------------------

def tolerant_parse_json(data):
    """
    JSON alert format (tolerant):
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

    payload = tolerant_parse_payload(payload_raw)

    # Prefer direction from payload (more reliable)
    action = payload.get("direction") or data.get("action", "").lower() or "buy"

    quantity = safe_float(data.get("quantity", 1))

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "sl": payload["sl"],
        "tp": payload["tp"]
    }


# ---------------------------------------------------------
# TOLERANT RAW PARSER
# ---------------------------------------------------------

def tolerant_parse_raw(raw: str):
    """
    RAW alert format (tolerant):
    BUY|NVDA|AutoTrader15M|SL:123|TP:130|EMA:xxx|ST:xxx
    """

    parts = [p.strip() for p in raw.split("|") if p.strip()]

    if len(parts) < 2:
        raise ValueError("Invalid raw alert")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])
    quantity = 1  # RAW alerts do not include quantity

    sl = None
    tp = None

    for part in parts:
        p = part.strip().upper()

        if p.startswith("SL:"):
            sl = safe_float(p.replace("SL:", "").strip())

        if p.startswith("TP:"):
            tp = safe_float(p.replace("TP:", "").strip())

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
# TOLERANT PAYLOAD PARSER
# ---------------------------------------------------------

def tolerant_parse_payload(payload: str):
    """
    Payload format (tolerant):
    BUY|NVDA|SL:123|TP:130
    BUY|NVDA|AutoTrader15M|SL:123|TP:130
    """

    parts = [p.strip() for p in payload.split("|") if p.strip()]

    if len(parts) < 2:
        raise ValueError("Invalid payload")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    sl = None
    tp = None

    for part in parts:
        p = part.strip().upper()

        if p.startswith("SL:"):
            sl = safe_float(p.replace("SL:", "").strip())

        if p.startswith("TP:"):
            tp = safe_float(p.replace("TP:", "").strip())

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
        return None
