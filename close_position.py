import session
from auth import auth
auth.login()
from trade_log import log_close

def close_position(position_id):
    """
    Close a position using Capital.com API + log closed trade.
    """

    # Ensure authenticated
    auth.login()

    try:
        url = f"{session.API_BASE}/positions/{position_id}/close"
        headers = session.get_headers()

        response = session.requests.post(url, headers=headers)

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"Failed to close position: {response.text}"
            }

        # Fetch updated positions to find the closed one
        positions = session.get_positions()

        # Find the closed position details
        for p in positions:
            if p["id"] == position_id:
                ticker = p["ticker"]
                size = p["size"]
                close_price = p["current_price"]
                pnl = p["profit"]

                # Log the closed trade
                log_close(ticker, size, close_price, pnl)

                break

        return {
            "status": "success",
            "message": f"Position {position_id} closed."
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
