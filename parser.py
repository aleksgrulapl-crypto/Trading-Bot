# ============================
# TradingView Alert Parser
# ============================

def parse_tradingview_alert(data):
    """
    Accepts either:
    - Raw TradingView alert string: "BUY|NVDA|SL:123|TP:130"
    - JSON TradingView alert dict: {"symbol": "NVDA", "action": "buy", "payload": "..."}
    """

    # -----------------------------------------------------
    # RAW STRING ALERT (contains "|")
    # -----------------------------------------------------
    if isinstance(data, str):
        return parse_raw_alert(data)

    # -----------------------------------------------------
    # JSON ALERT
    # -----------------------------------------------------
    symbol = data.get("symbol")
    action = data.get("action", "").lower()
    quantity = float(data.get("quantity", 1))

    payload_raw = data.get("payload", "")
    payload = parse_payload(payload_raw)

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "sl": payload["sl"],
        "tp": payload["tp"]
    }


# ---------------------------------------------------------
# RAW ALERT PARSER
# ---------------------------------------------------------

def parse_raw_alert(raw: str):
    """
    Raw TradingView alert format:
    BUY|NVDA|AutoTrader15M|SL:123.45|TP:130.22|EMA:xxx|ST:xxx
    """

    parts = raw.split("|")

    if len(parts) < 4:
        raise ValueError(f"Invalid raw alert format: {raw}")

    direction = parts[0].lower()
    symbol = parts[1]
    quantity = 1  # Raw alerts do not include quantity

    sl = None
    tp = None

    # Extract SL/TP from ANY position
    for part in parts:
        if part.startswith("SL:"):
            sl = float(part.replace("SL:", ""))
        if part.startswith("TP:"):
            tp = float(part.replace("TP:", ""))

    if sl is None or tp is None:
        raise ValueError(f"Missing SL/TP in alert: {raw}")

    return {
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp
    }


# ---------------------------------------------------------
# PAYLOAD PARSER (JSON alerts)
# ---------------------------------------------------------

def parse_payload(payload: str):
    """
    Payload format (JSON alerts):
    BUY|NVDA|SL:123.45|TP:130.22
    """

    parts = payload.split("|")

    if len(parts) < 4:
        raise ValueError(f"Invalid payload format: {payload}")

    direction = parts[0].lower()
    symbol = parts[1]

    sl = None
    tp = None

    for part in parts:
        if part.startswith("SL:"):
            sl = float(part.replace("SL:", ""))
        if part.startswith("TP:"):
            tp = float(part.replace("TP:", ""))

    if sl is None or tp is None:
        raise ValueError(f"Missing SL/TP in payload: {payload}")

    return {
        "direction": direction,
        "symbol": symbol,
        "sl": sl,
        "tp": tp
    }
