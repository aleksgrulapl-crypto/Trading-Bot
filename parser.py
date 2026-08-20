# tradingview_parser.py
# ============================
# TradingView Alert Parser (STRICT + EXIT-SIGNAL BLOCKING + NORMALIZED TF)
# ============================

from typing import Any, Dict, Optional

PLACEHOLDER_VALUES = {"{{alert_message}}", "", None}


def parse_tradingview_alert(data: Any) -> Dict[str, Any]:
    """
    Accepts:
      - Raw TradingView alert string: "BUY|NVDA|SL:123|TP:130|TF:5M"
      - JSON TradingView alert dict: {"symbol": "NVDA", "action": "buy", "payload": "..."}
    Returns either:
      - Valid parsed alert dict (blocked: False)
      - Blocked alert dict (blocked: True)
    """
    if isinstance(data, str):
        return parse_raw_alert_strict(data)

    if isinstance(data, dict):
        return parse_json_alert_strict(data)

    return block_alert("invalid_format", raw=data)


# ============================
# STRICT RAW ALERT PARSER
# ============================

def parse_raw_alert_strict(raw: str) -> Dict[str, Any]:
    if raw in PLACEHOLDER_VALUES:
        return block_alert("placeholder_payload", raw=raw)

    parts = [p.strip() for p in raw.split("|") if p is not None]

    # Minimal structure expected: direction|symbol|... (SL and TP required later)
    if len(parts) < 2:
        return block_alert("malformed_raw_alert", raw=raw)

    # Direction
    direction_raw = parts[0].strip().lower()
    if direction_raw.startswith("exit"):
        return block_alert("ignored_exit_signal", raw=raw)

    direction = direction_raw.split(" ")[0]
    if direction not in ("buy", "sell"):
        return block_alert("missing_direction", raw=raw)

    # Symbol
    symbol = normalise_symbol(parts[1])
    if not symbol:
        return block_alert("missing_symbol", raw=raw)

    sl = None
    tp = None
    timeframe = None

    # parse remaining parts for SL/TP/TF (support SL: / SL= / SL= )
    for part in parts[2:]:
        if not part:
            continue
        up = part.upper()
        if up.startswith("SL:") or up.startswith("SL="):
            sl = safe_float_or_none(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        elif up.startswith("TP:") or up.startswith("TP="):
            tp = safe_float_or_none(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        elif up.startswith("TF:") or up.startswith("TF="):
            timeframe = normalize_timeframe(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        # allow payloads that include key=value pairs separated by spaces
        else:
            # try to detect inline tokens like "SL 123" or "TP 130"
            tokens = part.split()
            if len(tokens) >= 2:
                key = tokens[0].upper().rstrip(":=")
                val = " ".join(tokens[1:])
                if key == "SL":
                    sl = safe_float_or_none(val)
                elif key == "TP":
                    tp = safe_float_or_none(val)
                elif key == "TF":
                    timeframe = normalize_timeframe(val)

    if sl is None or tp is None:
        return block_alert("missing_sl_tp", raw=raw)

    return {
        "blocked": False,
        "symbol": symbol,
        "action": direction,
        "quantity": 1,
        "sl": sl,
        "tp": tp,
        "timeframe": timeframe,
        "raw": raw
    }


# ============================
# STRICT JSON ALERT PARSER
# ============================

def parse_json_alert_strict(data: Dict[str, Any]) -> Dict[str, Any]:
    symbol = normalise_symbol(data.get("symbol") or data.get("ticker") or data.get("s"))
    direction_raw = (data.get("action") or data.get("side") or "").strip().lower()
    quantity = safe_float_or_none(data.get("quantity", 1))
    payload_raw = data.get("payload") or data.get("message") or data.get("text")

    if direction_raw.startswith("exit"):
        return block_alert("ignored_exit_signal", raw=data)

    direction = direction_raw.split(" ")[0] if direction_raw else None

    if not symbol:
        return block_alert("missing_symbol", raw=data)

    if direction not in ("buy", "sell"):
        return block_alert("missing_direction", raw=data)

    if quantity is None:
        return block_alert("invalid_quantity", raw=data)

    if payload_raw in PLACEHOLDER_VALUES:
        return block_alert("placeholder_payload", raw=data)

    # payload may be a dict already (some alert integrations send structured payload)
    if isinstance(payload_raw, dict):
        payload = payload_raw
    else:
        try:
            payload = parse_payload_strict(payload_raw)
        except Exception:
            return block_alert("malformed_payload", raw=data)

    # payload may be dict from parse_payload_strict or original dict
    sl = safe_float_or_none(payload.get("sl") if isinstance(payload, dict) else None)
    tp = safe_float_or_none(payload.get("tp") if isinstance(payload, dict) else None)
    tf_raw = payload.get("timeframe") if isinstance(payload, dict) else None

    if sl is None or tp is None:
        return block_alert("missing_sl_tp", raw=data)

    return {
        "blocked": False,
        "symbol": symbol,
        "action": direction,
        "quantity": quantity,
        "sl": sl,
        "tp": tp,
        "timeframe": normalize_timeframe(tf_raw),
        "raw": payload_raw
    }


# ============================
# STRICT PAYLOAD PARSER
# ============================

def parse_payload_strict(payload: Optional[str]) -> Dict[str, Any]:
    """
    Parse a payload string in the same strict format as raw alerts.
    Returns a dict with keys: direction, symbol, sl, tp, timeframe
    Raises ValueError on malformed payload.
    """
    if payload in PLACEHOLDER_VALUES:
        raise ValueError("placeholder payload")

    if not isinstance(payload, str):
        raise ValueError("payload must be a string")

    parts = [p.strip() for p in payload.split("|") if p is not None]

    if len(parts) < 2:
        raise ValueError("malformed payload")

    direction_raw = parts[0].strip().lower()
    if direction_raw.startswith("exit"):
        raise ValueError("exit signal ignored")

    direction = direction_raw.split(" ")[0]
    if direction not in ("buy", "sell"):
        raise ValueError("missing direction")

    symbol = normalise_symbol(parts[1])
    if not symbol:
        raise ValueError("missing symbol")

    sl = None
    tp = None
    timeframe = None

    for part in parts[2:]:
        if not part:
            continue
        up = part.upper()
        if up.startswith("SL:") or up.startswith("SL="):
            sl = safe_float_or_none(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        elif up.startswith("TP:") or up.startswith("TP="):
            tp = safe_float_or_none(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        elif up.startswith("TF:") or up.startswith("TF="):
            timeframe = normalize_timeframe(part.split(":", 1)[-1].split("=", 1)[-1].strip())
        else:
            tokens = part.split()
            if len(tokens) >= 2:
                key = tokens[0].upper().rstrip(":=")
                val = " ".join(tokens[1:])
                if key == "SL":
                    sl = safe_float_or_none(val)
                elif key == "TP":
                    tp = safe_float_or_none(val)
                elif key == "TF":
                    timeframe = normalize_timeframe(val)

    if sl is None or tp is None:
        raise ValueError("missing SL/TP")

    return {
        "direction": direction,
        "symbol": symbol,
        "sl": sl,
        "tp": tp,
        "timeframe": timeframe
    }


# ============================
# BLOCKED ALERT STRUCTURE
# ============================

def block_alert(reason: str, raw: Any) -> Dict[str, Any]:
    return {
        "blocked": True,
        "reason": reason,
        "raw": raw
    }


# ============================
# HELPERS
# ============================

def normalise_symbol(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    return str(symbol).strip().upper()


def safe_float_or_none(value: Any) -> Optional[float]:
    """
    Convert value to float safely. Accepts strings with commas.
    Returns None on failure.
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            v = value.replace(",", "").strip()
            return float(v)
        return float(value)
    except Exception:
        return None


def normalize_timeframe(tf: Optional[str]) -> Optional[str]:
    """
    Normalize timeframe strings:
      - "5"   -> "5M"
      - "5m"  -> "5M"
      - "15"  -> "15M"
      - "1h"  -> "1H"
      - "1H"  -> "1H"
    Returns None if input is falsy.
    """
    if not tf:
        return None

    tf_str = str(tf).strip()
    if not tf_str:
        return None

    tf_upper = tf_str.upper()

    # If numeric only → assume minutes
    if tf_upper.isdigit():
        return f"{tf_upper}M"

    # If already ends with M or H (case-insensitive), normalize to uppercase
    if tf_upper.endswith("M") or tf_upper.endswith("H"):
        return tf_upper

    # If ends with lowercase m/h (already handled by upper), fallback to returning uppercase
    # Otherwise return the original uppercased token as a best-effort normalization
    return tf_upper
