# parser.py — Clean TradingView alert + payload parser

def parse_tradingview_alert(json_data: dict):
    """
    Parses TradingView webhook JSON and extracts:
    - symbol
    - action (buy/sell)
    - quantity
    - payload (BUY|TICKER|SL:x|TP:y)
    """

    symbol = json_data.get("symbol")
    action = json_data.get("action", "").lower()
    quantity = float(json_data.get("quantity", 1))

    payload_raw = json_data.get("payload", "")
    payload = parse_payload(payload_raw)

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "direction": payload["direction"],
        "sl": payload["sl"],
        "tp": payload["tp"],
        "payload_symbol": payload["symbol"]
    }


def parse_payload(payload: str):
    """
    Payload format:
    BUY|NVDA|SL:123.45|TP:130.22
    SELL|AAPL|SL:200.10|TP:190.50
    """

    parts = payload.split("|")

    if len(parts) < 4:
        raise ValueError(f"Invalid payload format: {payload}")

    direction = parts[0].lower()
    symbol = parts[1]

    sl = float(parts[2].replace("SL:", ""))
    tp = float(parts[3].replace("TP:", ""))

    return {
        "direction": direction,
        "symbol": symbol,
        "sl": sl,
        "tp": tp
    }
