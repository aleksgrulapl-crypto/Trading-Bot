# notifications.py
# ============================
# EMAIL NOTIFICATIONS (position opened / closed)
# ============================
"""Sends an email directly to the configured recipient(s) when a position is
opened and/or closed, via plain SMTP (stdlib smtplib – no extra dependency).

Each event is independently toggleable so you can choose, e.g., close-only
notifications (the default) which include the trade's PnL and the current
account balance. See config.py for the NOTIFY_EMAIL_* / SMTP_* settings.

Sending happens on a background thread so a slow/unreachable mail server
never blocks the webhook request or the position-close sync loop; any
failure is logged and swallowed rather than raised.
"""

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from typing import Any, Dict, Optional

import config

logger = logging.getLogger("notifications")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [notify] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)


def _fmt_money(value: Any, prefix: str = "£") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{prefix}{float(value):,.2f}"
    except Exception:
        return str(value)


def _send_email_sync(subject: str, body: str) -> bool:
    """Actually connect to SMTP and send. Runs on the calling thread."""
    to_addrs = [a.strip() for a in (config.NOTIFY_EMAIL_TO or "").split(",") if a.strip()]
    if not to_addrs:
        logger.warning("_send_email_sync: NOTIFY_EMAIL_TO not configured; skipping '%s'", subject)
        return False
    if not config.SMTP_HOST or not config.SMTP_USERNAME or not config.SMTP_PASSWORD:
        logger.warning("_send_email_sync: SMTP credentials not fully configured; skipping '%s'", subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.NOTIFY_EMAIL_FROM or config.SMTP_USERNAME
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    try:
        if config.SMTP_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
                server.starttls(context=context)
                server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
                server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
                server.send_message(msg)
        logger.info("_send_email_sync: sent '%s' to %s", subject, ", ".join(to_addrs))
        return True
    except Exception:
        logger.exception("_send_email_sync: failed to send '%s'", subject)
        return False


def _send_email(subject: str, body: str) -> bool:
    if not getattr(config, "NOTIFY_EMAIL_ENABLED", False):
        return False
    threading.Thread(target=_send_email_sync, args=(subject, body), daemon=True).start()
    return True


def notify_position_opened(trade: Dict[str, Any]) -> bool:
    """Email when a position is opened, if NOTIFY_EMAIL_ON_OPEN is enabled."""
    if not getattr(config, "NOTIFY_EMAIL_ON_OPEN", False):
        return False
    if not trade:
        return False

    ticker = trade.get("ticker") or "?"
    side = str(trade.get("side") or "?").upper()
    subject = f"Position opened: {ticker} {side}"
    body = (
        f"A new position was opened.\n\n"
        f"Ticker: {ticker}\n"
        f"Side: {side}\n"
        f"Size: {trade.get('size')}\n"
        f"Entry price: {trade.get('entry_price')}\n"
        f"Deal ID: {trade.get('dealId')}\n"
        f"Opened at: {trade.get('time_entered')}\n"
    )
    return _send_email(subject, body)


def notify_position_closed(trade: Dict[str, Any], balance: Optional[float] = None) -> bool:
    """Email when a position is closed, if NOTIFY_EMAIL_ON_CLOSE is enabled.
    Includes the trade's PnL and (when available) the current account balance.
    """
    if not getattr(config, "NOTIFY_EMAIL_ON_CLOSE", False):
        return False
    if not trade:
        return False

    ticker = trade.get("ticker") or "?"
    side = str(trade.get("side") or "?").upper()
    pnl = trade.get("pnl")
    pnl_gbp = trade.get("pnl_gbp")
    display_pnl = pnl_gbp if pnl_gbp is not None else pnl
    try:
        result = "WIN" if float(display_pnl) > 0 else ("LOSS" if float(display_pnl) < 0 else "FLAT")
    except (TypeError, ValueError):
        result = "UNKNOWN"

    subject = f"Position closed: {ticker} {side} ({result}, {_fmt_money(display_pnl)})"
    body = (
        f"A position was closed.\n\n"
        f"Ticker: {ticker}\n"
        f"Side: {side}\n"
        f"Entry price: {trade.get('entry_price')}\n"
        f"Exit price: {trade.get('exit_price')}\n"
        f"PnL: {_fmt_money(display_pnl)}\n"
        f"Closed at: {trade.get('time_exited')}\n"
        f"Account balance: {_fmt_money(balance)}\n"
    )
    return _send_email(subject, body)
