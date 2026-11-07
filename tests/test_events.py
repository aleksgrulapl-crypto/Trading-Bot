import time
from datetime import date

import pytest

import config
import events


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVENTS_CACHE_PATH", str(tmp_path / "ev.json"), raising=False)
    monkeypatch.setattr(config, "FINNHUB_API_KEY", "k", raising=False)


def test_score_headlines():
    assert events.score_headlines(["Stock surges after earnings beat"])["sentiment"] == "Bullish"
    assert events.score_headlines(["Shares plunge on lawsuit"])["sentiment"] == "Bearish"
    assert events.score_headlines(["Company holds meeting"])["sentiment"] == "Neutral"


def test_normalize_symbol():
    assert events.normalize_symbol("nvda.us") == "NVDA"


def test_conflict_and_info():
    assert events.is_conflict("Long", "Bearish")
    assert events.is_conflict("Short", "Bullish")
    assert not events.is_conflict("Long", "Bullish")
    cache = {"NVDA": {"sentiment": "Bearish", "earnings_date": "2026-11-17", "headlines": ["x"]}}
    info = events.event_info("NVDA", "Long", cache, today=date(2026, 11, 7))
    assert info["conflict"] and info["earnings_days"] == 10


def test_refresh_and_attach(monkeypatch):
    def fake_get(endpoint, params):
        if endpoint == "calendar/earnings":
            return {"earningsCalendar": [{"date": "2099-01-01"}]}
        return [{"headline": "Shares plunge on lawsuit", "datetime": 1}]

    monkeypatch.setattr(events, "_get", fake_get)
    events.refresh_symbol("NVDA", now=time.time())
    trades = [{"ticker": "NVDA", "side": "Long"}, {"ticker": "ZZZ", "side": "Long"}]
    events.attach_to_trades(trades)
    assert trades[0]["events"]["conflict"] is True
    assert "events" not in trades[1]


def test_refresh_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(events, "_get", boom)
    assert events.refresh_symbol("NVDA") is None


def test_alerts_sent_once(monkeypatch):
    import notifications

    sent = []
    monkeypatch.setattr(notifications, "is_enabled", lambda: True)
    monkeypatch.setattr(notifications, "send_event_alert", lambda s, b: sent.append(s))
    events._save({"NVDA": {"sentiment": "Bearish", "earnings_date": date.today().isoformat(), "headlines": []}})
    trades = [{"ticker": "NVDA", "side": "Long"}]
    assert events.check_alerts(trades) == 2
    assert events.check_alerts(trades) == 0
    assert len(sent) == 2
