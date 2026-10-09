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

import json
import logging
import os
import smtplib
import ssl
import tempfile
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

# Default recipient applied the first time the dashboard's Owner-only
# "send to email" toggle is switched on (if no recipient is already
# configured via NOTIFY_EMAIL_TO or a prior toggle).
DEFAULT_RECIPIENT_EMAIL = "aleksgrulapl@gmail.com"

SETTINGS_PATH = getattr(config, "NOTIFY_SETTINGS_PATH", "/data/notify_settings.json")
_settings_lock = threading.Lock()


def _atomic_write(path: str, data: Any) -> bool:
    try:
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("_atomic_write: failed to persist %s", path)
        return False


def load_settings(path: str = SETTINGS_PATH) -> Dict[str, Any]:
    """Load the persisted dashboard-toggle settings ({} if absent/invalid)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("load_settings: failed to read %s", path)
        return {}


def save_settings(settings: Dict[str, Any], path: str = SETTINGS_PATH) -> bool:
    return _atomic_write(path, settings)


def is_enabled() -> bool:
    """Master on/off switch for email notifications. A persisted dashboard
    toggle overrides the NOTIFY_EMAIL_ENABLED env default when present."""
    settings = load_settings()
    if "enabled" in settings:
        return bool(settings["enabled"])
    return bool(getattr(config, "NOTIFY_EMAIL_ENABLED", False))


def get_recipient() -> str:
    """Return the configured recipient address(es), preferring a persisted
    dashboard override over the NOTIFY_EMAIL_TO env default."""
    settings = load_settings()
    to = settings.get("to")
    if to:
        return to
    return getattr(config, "NOTIFY_EMAIL_TO", "") or ""


def set_enabled(enabled: bool) -> Dict[str, Any]:
    """Toggle email notifications on/off from the dashboard. The first time
    it's switched on with no recipient configured anywhere, default the
    recipient to DEFAULT_RECIPIENT_EMAIL."""
    with _settings_lock:
        settings = load_settings()
        settings["enabled"] = bool(enabled)
        if enabled and not settings.get("to") and not (getattr(config, "NOTIFY_EMAIL_TO", "") or ""):
            settings["to"] = DEFAULT_RECIPIENT_EMAIL
        save_settings(settings)
        return settings


def set_recipient(to: str) -> Dict[str, Any]:
    with _settings_lock:
        settings = load_settings()
        settings["to"] = to
        save_settings(settings)
        return settings


def _fmt_money(value: Any, prefix: str = "£") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{prefix}{float(value):,.2f}"
    except Exception:
        return str(value)


def _fmt_datetime(value: Any) -> str:
    """Render an ISO-8601 timestamp (as produced by utils.uk_timestamp) in a
    human-friendly form, e.g. '09 Oct 2026, 05:15'. Falls back to the raw
    value if it can't be parsed."""
    if not value:
        return "n/a"
    try:
        import datetime
        s = str(value)
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return str(value)


def _send_email_sync(subject: str, body: str) -> bool:
    """Actually connect to SMTP and send. Runs on the calling thread."""
    to_addrs = [a.strip() for a in (get_recipient() or "").split(",") if a.strip()]
    if not to_addrs:
        logger.warning("_send_email_sync: no recipient configured; skipping '%s'", subject)
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
    if not is_enabled():
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
        f"Opened at: {_fmt_datetime(trade.get('time_entered'))}\n"
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

    # The balance we're handed is fetched from the broker right after the
    # close request returns, before the broker has necessarily settled this
    # trade's realised P&L into the account's funds figure. Add this trade's
    # own PnL on top so the reported balance reflects the win/loss that was
    # just applied, rather than a pre-settlement snapshot.
    balance_after = balance
    if balance is not None and display_pnl is not None:
        try:
            balance_after = float(balance) + float(display_pnl)
        except (TypeError, ValueError):
            balance_after = balance

    body = (
        f"A position was closed.\n\n"
        f"Ticker: {ticker}\n"
        f"Side: {side}\n"
        f"Size: {trade.get('size')}\n"
        f"Entry price: {trade.get('entry_price')}\n"
        f"Exit price: {trade.get('exit_price')}\n"
        f"PnL: {_fmt_money(display_pnl)}\n"
        f"Closed at: {_fmt_datetime(trade.get('time_exited'))}\n"
        f"Account balance: {_fmt_money(balance_after)}\n"
    )
    return _send_email(subject, body)
