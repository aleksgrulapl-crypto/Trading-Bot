# ============================
# TradingView Alert Parser (STRICT + BLOCKED TRADE SUPPORT)
# ============================

PLACEHOLDER_VALUES = {"{{alert_message}}", "", None}


def parse_tradingview_alert(data):
    """
    Accepts:
    - Raw TradingView alert string: "BUY|NVDA|SL:123|TP:130"
    - JSON TradingView alert dict: {"symbol": "NVDA", "action": "buy", "payload": "..."}
    Returns either:
    - Valid parsed alert dict
    - Blocked alert dict (blocked=True)
    """

    # RAW STRING ALERT
    if isinstance(data, str):
        return parse_raw_alert_strict(data)

    # JSON ALERT
    if isinstance(data, dict):
        return parse_json_alert_strict(data)

    return block_alert("invalid_format", raw=data)


# ============================
# STRICT RAW ALERT PARSER
# ============================

def parse_raw_alert_strict(raw: str):
    """
    Expected clean format:
    BUY|NVDA|SL:123|TP:130
    """

    if raw in PLACEHOLDER_VALUES:
        return block_alert("placeholder_payload", raw=raw)

    parts = raw.split("|")

    if len(parts) < 4:
        return block_alert("malformed_raw_alert", raw=raw)

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    if not direction or direction not in ("buy", "sell"):
        return block_alert("missing_direction", raw=raw)

    if not symbol:
        return block_alert("missing_symbol", raw=raw)

    sl = None
    tp = None

    for part in parts:
        part = part.strip()

        if part.upper().startswith("SL:"):
            sl = safe_float_or_none(part.replace("SL:", "").strip())

        if part.upper().startswith("TP:"):
            tp = safe_float_or_none(part.replace("TP:", "").strip())

    if sl is None or tp is None:
        return block_alert("missing_sl_tp", raw=raw)

    return {
        "blocked": False,
        "symbol": symbol,
        "action": direction,
        "quantity": 1,
        "sl": sl,
        "tp": tp,
        "raw": raw
    }


# ============================
# STRICT JSON ALERT PARSER
# ============================

def parse_json_alert_strict(data):
    symbol = normalise_symbol(data.get("symbol"))
    direction = (data.get("action") or "").lower()
    quantity = safe_float_or_none(data.get("quantity", 1))
    payload_raw = data.get("payload")

    # Basic validation
    if not symbol:
        return block_alert("missing_symbol", raw=data)

    if direction not in ("buy", "sell"):
        return block_alert("missing_direction", raw=data)

    if quantity is None:
        return block_alert("invalid_quantity", raw=data)

    # Payload validation
    if payload_raw in PLACEHOLDER_VALUES:
        return block_alert("placeholder_payload", raw=data)

    # Parse payload
    try:
        payload = parse_payload_strict(payload_raw)
    except Exception:
        return block_alert("malformed_payload", raw=data)

    sl = payload.get("sl")
    tp = payload.get("tp")

    if sl is None or tp is None:
        return block_alert("missing_sl_tp", raw=data)

    return {
        "blocked": False,
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp,
        "raw": payload_raw
    }


# ============================
# STRICT PAYLOAD PARSER
# ============================

def parse_payload_strict(payload: str):
    """
    Expected clean format:
    BUY|NVDA|SL:123|TP:130
    """

    if payload in PLACEHOLDER_VALUES:
        raise ValueError("placeholder payload")

    parts = payload.split("|")

    if len(parts) < 4:
        raise ValueError("malformed payload")

    direction = parts[0].lower()
    symbol = normalise_symbol(parts[1])

    if direction not in ("buy", "sell"):
        raise ValueError("missing direction")

    if not symbol:
        raise ValueError("missing symbol")

    sl = None
    tp = None

    for part in parts:
        part = part.strip()

        if part.upper().startswith("SL:"):
            sl = safe_float_or_none(part.replace("SL:", "").strip())

        if part.upper().startswith("TP:"):
            tp = safe_float_or_none(part.replace("TP:", "").strip())

    if sl is None or tp is None:
        raise ValueError("missing SL/TP")

    return {
        "direction": direction,
        "symbol": symbol,
        "sl": sl,
        "tp": tp
    }


# ============================
# BLOCKED ALERT STRUCTURE
# ============================

def block_alert(reason, raw):
    """
    Returns a structured blocked alert object.
    Logged by webhook and displayed in dashboard.
    """

    return {
        "blocked": True,
        "reason": reason,
        "raw": raw
    }


# ============================
# HELPERS
# ============================

def normalise_symbol(symbol: str):
    if not symbol:
        return None
    return symbol.strip().upper()


def safe_float_or_none(value):
    try:
        return float(value)
    except:
        return None
