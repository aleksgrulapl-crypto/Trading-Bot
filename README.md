# AG Capital Trading Bot

A Python/Flask-based automated trading bot for the Capital.com CFD API, with a real-time web dashboard and structured trade log.

---

## Features

- **Webhook receiver** (`/webhook`) — accepts TradingView alerts, suppresses duplicate deliveries, and handles idempotent broker position events
- **Automated order placement** — sizes positions based on available equity, leverage, per-trade caps, and per-ticker minimums
- **Trade log** (`/data/trade_log.json`) — thread-safe JSON store with atomic writes and timestamped backups
- **Real-time dashboard** (`/dashboard`) — live UK clock, account metrics, open positions, and completed-trade history
- **Scheduler** — runs the daily report and trailing-stop sync in the background

---

## Quick Start

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.9+ |
| pip | 23+ |

### Install dependencies

```bash
pip install -r requirements.txt
```

### Environment variables

Copy the table below and export the values in your environment or a `.env` file:

| Variable | Description | Required |
|----------|-------------|----------|
| `CAPITAL_API_KEY` | Capital.com API key | ✅ |
| `CAPITAL_USERNAME` | Capital.com account username | ✅ |
| `CAPITAL_PASSWORD` | Capital.com account password | ✅ |
| `DASHBOARD_PASSWORD` | Password for the `/dashboard` login page | ✅ |
| `FX_USD_GBP` | USD→GBP conversion rate (default `0.78`) | ⬜ |
| `TRADE_LOG_PATH` | Path for the trade-log JSON file (default `/data/trade_log.json`) | ⬜ |
| `DEBUG_LOGS` | Set to `true` to enable verbose logging | ⬜ |
| `PORT` | HTTP port (default `5000`) | ⬜ |
| `RISK_PER_TRADE` | Fraction of equity risked per trade (default `0.50`) | ⬜ |
| `LEVERAGE` | Leverage multiplier (default `5`) | ⬜ |
| `EQUITY_PERCENT` | Fraction of available margin used per trade before caps (default `0.50`) | ⬜ |
| `MAX_EQUITY_PER_TRADE` | Maximum account equity allocated to a trade before leverage, in GBP (default `200`) | ⬜ |
| `MAX_EXPOSURE_PER_TRADE` | Maximum leveraged exposure per trade in GBP (default `1000`) | ⬜ |
| `FIXED_SL_PERC` | Stop-loss distance as a fraction of entry price (default `0.20` = `20%`, capping loss at £200 on £1000 max exposure) | ⬜ |
| `FIXED_TP_PERC` | Take-profit distance as a fraction of entry price (default `0.40` = `40%`, capping gain at £400 on £1000 max exposure) | ⬜ |
| `TRAIL_ACTIVATION_PERC` | Profit threshold to activate trailing SL (default `0.02` = `2%`) | ⬜ |
| `TRAIL_ACTIVATION_TP_FRACTION` | Fraction of the TP move used as an alternative (often earlier) activation threshold (default `0.25` = `25%`) | ⬜ |
| `TRAIL_SL_PERC` | Portion of unrealized profit locked by trailing SL as soon as it activates (default `0.65` = `65%`) | ⬜ |
| `TRAIL_TIGHTEN_STEP_PERC` | How much the locked-in portion ramps up per activation-multiple of profit beyond 1x, so the SL follows more closely the deeper a trade goes in-profit (default `0.15` = `15%` per step) | ⬜ |
| `TRAIL_MAX_PERC` | Ceiling on the locked-in portion as profit keeps extending (default `0.95` = `95%`) | ⬜ |
| `NOTIFY_EMAIL_ENABLED` | Master switch for email notifications (default `False`) | ⬜ |
| `NOTIFY_EMAIL_ON_OPEN` | Send an email when a position opens (default `False`) | ⬜ |
| `NOTIFY_EMAIL_ON_CLOSE` | Send an email when a position closes, with PnL and account balance (default `True`) | ⬜ |
| `NOTIFY_EMAIL_TO` | Comma-separated recipient email address(es) | ⬜ |
| `NOTIFY_EMAIL_FROM` | "From" address on the email (default: `SMTP_USERNAME`) | ⬜ |
| `SMTP_HOST` | SMTP server hostname used to send notification emails | ⬜ |
| `SMTP_PORT` | SMTP server port (default `587`) | ⬜ |
| `SMTP_USERNAME` | SMTP login username | ⬜ |
| `SMTP_PASSWORD` | SMTP login password (use an app password where supported) | ⬜ |
| `SMTP_USE_TLS` | Use STARTTLS on `SMTP_PORT` (default `True`); set to `False` for implicit SSL | ⬜ |
| `NOTIFY_SETTINGS_PATH` | Path for the dashboard's persisted email-toggle/recipient override (default `/data/notify_settings.json`) | ⬜ |

### Email notifications

The bot can email you directly when a position is **opened** and/or **closed**
(the close email includes the trade's PnL and the current account balance).
Each event is toggled independently via `NOTIFY_EMAIL_ON_OPEN` /
`NOTIFY_EMAIL_ON_CLOSE`, so you can enable just one (e.g. close-only, which is
the default) or both. Set `NOTIFY_EMAIL_ENABLED=True`, fill in the `SMTP_*`
settings for your mail provider (e.g. Gmail: `smtp.gmail.com`, port `587`,
an [app password](https://support.google.com/accounts/answer/185833)), and
set `NOTIFY_EMAIL_TO` to the address(es) that should receive the emails.

The Owner can also flip the master switch at runtime from the dashboard: an
"✉ Email Alerts: ON/OFF" button next to **Account & Open Positions** (visible
to the Owner role only). The first time it's switched on with no recipient
configured, it defaults the recipient to `aleksgrulapl@gmail.com`; this
choice is persisted to `NOTIFY_SETTINGS_PATH` (default `/data/notify_settings.json`)
and overrides `NOTIFY_EMAIL_ENABLED`/`NOTIFY_EMAIL_TO` from then on.

### Run locally

```bash
export CAPITAL_API_KEY=...
export CAPITAL_USERNAME=...
export CAPITAL_PASSWORD=...
export DASHBOARD_PASSWORD=my_secure_password

python webhook.py
```

The app will be available at `http://localhost:5000`.

### Run with Docker

```bash
docker build -t trading-bot .
docker run -p 5000:5000 \
  -e CAPITAL_API_KEY=... \
  -e CAPITAL_USERNAME=... \
  -e CAPITAL_PASSWORD=... \
  -e DASHBOARD_PASSWORD=my_secure_password \
  -v /host/data:/data \
  trading-bot
```

---

## API Endpoints

### Webhook

| Method | Path | Description |
|--------|------|-------------|
| POST | `/webhook` | Receive TradingView or broker position events |

**Payload (TradingView alert):**
```json
{
  "symbol": "NVDA",
  "action": "buy",
  "sl": 120.00,
  "tp": 140.00,
  "timeframe": "1h"
}
```

**Payload (broker position / idempotent upsert):**
```json
{
  "dealId": "DID123",
  "position": { "direction": "BUY", "size": 10, "level": 130.50 },
  "market": { "symbol": "NVDA" }
}
```

Every webhook response includes a `cid` (correlation ID) for end-to-end tracing.

### Dashboard

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard` | Full dashboard page (requires login) |
| GET | `/dashboard/data` | Partial HTML + JSON data (AJAX refresh) |
| POST | `/dashboard/close/<position_id>` | Close a live position via the UI |
| GET | `/dashboard/login` | Login page |
| POST | `/dashboard/login` | Submit login credentials |
| GET | `/dashboard/logout` | Log out |

### Debug (development only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/debug/tokens` | Check auth token status |
| GET | `/debug/epic/<symbol>` | Resolve a symbol to its EPIC |
| GET | `/debug/market/<epic>` | Fetch live market snapshot |
| GET | `/debug/positions` | Raw + enriched positions |
| GET | `/debug/sizing/<symbol>/<action>/<price>/<sl>/<tp>` | Test position sizing |
| GET | `/debug/history` | Raw transaction history |

---

## Trade Log Format

Each entry in `/data/trade_log.json` is a JSON object:

```json
{
  "dealId": "DID123",
  "dealReference": "DREF456",
  "ticker": "NVDA",
  "side": "Long",
  "size": 10.0,
  "entry_price": 130.50,
  "exit_price": 145.00,
  "time_entered": "2026-01-15T09:30:00Z",
  "time_exited": "2026-01-16T14:22:00Z",
  "time_entered_human": "15-01-2026 09:30:00",
  "time_exited_human": "16-01-2026 14:22:00",
  "pnl": 145.00,
  "pnl_gbp": 113.10,
  "status": "CLOSED",
  "notes": "Imported from webhook"
}
```

---

## Architecture

```
webhook.py          Flask app + webhook route + auth bootstrap
├── dashboard.py    Blueprint: /dashboard, /dashboard/data, /dashboard/close/<id>
├── trade_log.py    Thread-safe JSON trade log (lock, atomic write, backup rotation)
├── close_position.py  Broker close + trade log update
├── order.py        Place broker orders
├── sizing.py       Position sizing (equity%, leverage, per-trade caps, per-ticker mins)
├── session.py      Broker HTTP client + position enrichment
├── auth.py         Token auth + refresh
├── scheduler.py    Background jobs (daily report, trailing stops)
└── config.py       All configuration constants + env overrides
```

---

## Tests

```bash
pip install pytest
pytest reconcile_test.py -v
```

---

## Deployment (Heroku / Render / Railway)

1. Set all required environment variables in your platform's dashboard.
2. Ensure the `/data` volume is mounted as a persistent disk so `trade_log.json` survives restarts.
3. The `Procfile` already contains the correct worker command.

---

## Security Notes

- Change `DASHBOARD_PASSWORD` from the default before deploying.
- Never commit credentials to the repository.
- The `/debug/*` endpoints should be restricted or disabled in production.
