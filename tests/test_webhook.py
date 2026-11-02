"""Tests for webhook: payloads, processing, auth, gating."""

import json
import os
import tempfile
import threading
import time
import sys
import pytest

import config


def _make_tmp_log(initial_trades=None):
    """Create a temp JSON file holding *initial_trades* (default: [])."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(initial_trades or [], f)
    return path


class TestWebhookPayloadValidation:
    """Tests for webhook._validate_webhook_payload."""

    def _validate(self, payload):
        # Import lazily to avoid Flask app init at collection time
        import importlib
        # We need to import just the validation function without starting the app
        # Since webhook.py creates the Flask app at module level, we patch out
        # the heavy initialisation by importing the function after sys.path setup.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import webhook as wh
        return wh._validate_webhook_payload(payload)

    def test_valid_payload_passes(self):
        result = self._validate({"entry_price": 100, "size": 10, "side": "buy"})
        assert result is None

    def test_zero_entry_price_fails(self):
        result = self._validate({"entry_price": 0, "size": 10})
        assert result is not None
        assert "entry_price" in result

    def test_negative_size_fails(self):
        result = self._validate({"entry_price": 100, "size": -5})
        assert result is not None
        assert "size" in result

    def test_invalid_side_fails(self):
        result = self._validate({"entry_price": 100, "size": 10, "side": "GARBAGE"})
        assert result is not None
        assert "side" in result.lower() or "direction" in result.lower()

    def test_missing_optional_fields_pass(self):
        # entry_price and size absent – validation only checks PRESENT fields
        result = self._validate({"dealId": "D1"})
        assert result is None

    def test_non_numeric_entry_price_fails(self):
        result = self._validate({"entry_price": "not_a_number", "size": 10})
        assert result is not None


class TestWebhookProcessing:
    def test_is_broker_position_event_ignores_position_id_only_payload(self):
        import webhook

        assert webhook._is_broker_position_event({
            "position": {"id": "tv-alert-123", "direction": "BUY", "size": 0.48, "level": 161.81, "createdDate": "2026-09-10T11:46:00Z"},
            "market": {"epic": "ORCL", "symbol": "Oracle Corporation"},
        }) is False

    def test_close_like_payload_is_deferred_until_broker_confirmation(self, monkeypatch):
        import webhook

        upserts = []

        def _fake_upsert(payload):
            upserts.append(payload)
            return payload

        monkeypatch.setattr(webhook, "upsert_open_trade", _fake_upsert)

        result = webhook.process_webhook_payload({
            "dealId": "D-CLOSE",
            "market": {"epic": "STX", "symbol": "Seagate Technology"},
            "price": 791.71,
            "closedDate": "2026-09-08T12:00:00Z",
        })

        assert result["action"] == "close_deferred"
        assert result["dealId"] == "D-CLOSE"
        assert upserts == [], "Webhook close hints must not mutate the trade log before broker confirmation"

    def test_broker_payload_prefers_epic_for_ticker_matching(self, monkeypatch):
        import webhook

        captured = {}

        def _fake_upsert(payload):
            captured["payload"] = payload
            return dict(payload, status="OPEN")

        monkeypatch.setattr(webhook, "upsert_open_trade", _fake_upsert)
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type(
                "Resp",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {"position": {"dealId": "D-OPEN"}},
                },
            )(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-OPEN",
                "direction": "BUY",
                "size": 1.2,
                "level": 790.25,
                "createdDate": "2026-09-08T10:00:00Z",
            },
            "market": {"epic": "STX", "symbol": "Seagate Technology"},
        })

        assert result["action"] == "upserted"
        assert captured["payload"]["ticker"] == "STX"

    def test_broker_open_payload_is_deferred_when_position_not_open(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type(
                "Resp",
                (),
                {
                    "status_code": 404,
                    "text": "position not found",
                    "json": lambda self: {"errorCode": "position_not_found"},
                },
            )(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-PHANTOM",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "position_not_open_at_broker"
        assert upserts == []

    def test_broker_open_payload_with_only_dealreference_is_deferred(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))

        result = webhook.process_webhook_payload({
            "position": {
                "dealReference": "REF-ONLY-1",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "awaiting_dealid_confirmation"
        assert upserts == []

    def test_broker_open_payload_is_deferred_when_position_check_inconclusive(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type("Resp", (), {"status_code": 429})(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-UNCLEAR",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "broker_position_check_inconclusive"
        assert upserts == []

    def test_broker_open_payload_treats_http_400_as_inconclusive(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type("Resp", (), {"status_code": 400})(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-400",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "broker_position_check_inconclusive"
        assert upserts == []

    def test_broker_open_payload_treats_404_without_not_found_body_as_inconclusive(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type("Resp", (), {"status_code": 404, "text": "", "json": lambda self: {}})(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-404-UNK",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "broker_position_check_inconclusive"
        assert upserts == []

    def test_broker_open_payload_treats_not_found_400_as_not_open(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type(
                "Resp",
                (),
                {
                    "status_code": 400,
                    "text": "Position not found",
                    "json": lambda self: {"errorCode": "position_not_found"},
                },
            )(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-404-LIKE",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "position_not_open_at_broker"
        assert upserts == []

    def test_broker_open_payload_treats_unmatched_200_body_as_inconclusive(self, monkeypatch):
        import webhook

        upserts = []
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: upserts.append(payload))
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"position": {"dealId": "DIFFERENT"}}},
            )(),
        )

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-EXPECTED",
                "direction": "BUY",
                "size": 1.0,
                "level": 100.5,
            },
            "market": {"epic": "NVDA", "symbol": "NVIDIA"},
        })

        assert result["action"] == "open_deferred"
        assert result["reason"] == "broker_position_check_inconclusive"
        assert upserts == []

    def test_position_id_is_not_treated_as_broker_deal_id(self, monkeypatch):
        import webhook

        captured = {}

        def _fake_upsert(payload):
            captured["payload"] = payload
            return dict(payload, status="OPEN")

        monkeypatch.setattr(webhook, "upsert_open_trade", _fake_upsert)

        result = webhook.process_webhook_payload({
            "position": {
                "id": "tv-alert-123",
                "direction": "BUY",
                "size": 0.48,
                "level": 161.81,
                "createdDate": "2026-09-10T11:46:00Z",
            },
            "market": {"epic": "ORCL", "symbol": "Oracle Corporation"},
        })

        assert result["action"] == "upserted"
        assert captured["payload"]["dealId"] is None

    def test_broker_open_payload_infers_existing_hedge_source(self, monkeypatch):
        import webhook

        captured = {}
        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{
                "dealId": None,
                "dealReference": "REF-HEDGE-1",
                "ticker": "INTC",
                "side": "short",
                "status": "OPEN",
                "trade_source": "hedge",
            }],
        )
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda method, url, **kwargs: type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"position": {"dealId": "D-HEDGE-1"}}},
            )(),
        )
        monkeypatch.setattr(webhook, "upsert_open_trade", lambda payload: captured.setdefault("payload", payload) or payload)

        result = webhook.process_webhook_payload({
            "position": {
                "dealId": "D-HEDGE-1",
                "dealReference": "REF-HEDGE-1",
                "direction": "SELL",
                "size": 1.0,
                "level": 99.5,
            },
            "market": {"epic": "INTC", "symbol": "Intel"},
        })

        assert result["action"] == "upserted"
        assert captured["payload"]["trade_source"] == "hedge"

    def test_trade_lock_windows_support_half_hour_ranges(self, monkeypatch):
        import webhook

        monkeypatch.setattr(webhook, "TRADE_LOCK_ENABLED", True)
        monkeypatch.setattr(
            webhook.config,
            "TRADE_LOCK_WINDOWS",
            [(-1, 8 * 60, 9 * 60 + 30), (-1, 13 * 60, 15 * 60)],
        )

        class _FakeDateTime:
            @staticmethod
            def now(_tz):
                return type("T", (), {"hour": 9, "minute": 15, "weekday": lambda self: 2})()

        monkeypatch.setattr(webhook, "datetime", _FakeDateTime)
        assert webhook._is_trade_locked_now() is True

        class _FakeDateTime2:
            @staticmethod
            def now(_tz):
                return type("T", (), {"hour": 10, "minute": 0, "weekday": lambda self: 2})()

        monkeypatch.setattr(webhook, "datetime", _FakeDateTime2)
        assert webhook._is_trade_locked_now() is False

    def test_trade_lock_windows_support_weekday_restriction(self, monkeypatch):
        import webhook

        monkeypatch.setattr(webhook, "TRADE_LOCK_ENABLED", True)
        # Monday-only 08:30-09:30 window (weekday 0 = Monday).
        monkeypatch.setattr(webhook.config, "TRADE_LOCK_WINDOWS", [(0, 8 * 60 + 30, 9 * 60 + 30)])

        class _FakeMonday:
            @staticmethod
            def now(_tz):
                return type("T", (), {"hour": 9, "minute": 0, "weekday": lambda self: 0})()

        monkeypatch.setattr(webhook, "datetime", _FakeMonday)
        assert webhook._is_trade_locked_now() is True

        class _FakeTuesday:
            @staticmethod
            def now(_tz):
                return type("T", (), {"hour": 9, "minute": 0, "weekday": lambda self: 1})()

        monkeypatch.setattr(webhook, "datetime", _FakeTuesday)
        assert webhook._is_trade_locked_now() is False

    def test_same_ticker_signal_can_scale_in_when_capacity_remains(self, monkeypatch):
        import webhook

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "tradingview"}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "buy"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook, "calculate_size", lambda **kwargs: {"blocked": False, "size": 1.0})
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        called = {}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            called["args"] = (epic, action, size, trade_source)
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "buy"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert called["args"] == ("INTC", "buy", 1.0, "tradingview")

    def test_same_ticker_signal_is_blocked_when_sizing_capacity_is_exhausted(self, monkeypatch, tmp_path):
        import webhook
        import gating

        monkeypatch.setattr(gating.config, "REJECTED_TRADES_PATH", str(tmp_path / "rej.json"), raising=False)

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "tradingview"}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "buy"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook, "calculate_size", lambda **kwargs: {"blocked": True, "reason": "max_ticker_equity_reached"})

        called = {"place_order": 0}

        def _fake_place_order(*args, **kwargs):
            called["place_order"] += 1
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "buy"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "blocked"
        assert body.get("reason") == "max_ticker_equity_reached"
        assert called["place_order"] == 0

    def test_opposite_signal_is_logged_as_hedge_source(self, monkeypatch):
        import webhook

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "tradingview"}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "sell"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook, "calculate_size", lambda **kwargs: {"blocked": False, "size": 1.0})
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        captured = {}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            captured["trade_source"] = trade_source
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "sell"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert captured["trade_source"] == "hedge"

    def test_hedge_signal_ignores_opposite_side_ticker_capacity(self, monkeypatch):
        import webhook

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "tradingview"}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "sell"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        captured = {}

        def _fake_calculate_size(**kwargs):
            captured["ignore_opposite_side_for_ticker_limits"] = kwargs.get("ignore_opposite_side_for_ticker_limits")
            return {"blocked": False, "size": 1.0}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            captured["trade_source"] = trade_source
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "calculate_size", _fake_calculate_size)
        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "sell"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert captured["ignore_opposite_side_for_ticker_limits"] is True
        assert captured["trade_source"] == "hedge"

    def test_hedge_signal_passes_hedged_trade_size_as_override(self, monkeypatch):
        import webhook

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "tradingview", "size": 7.5}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "sell"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        captured = {}

        def _fake_calculate_size(**kwargs):
            captured["hedge_size_override"] = kwargs.get("hedge_size_override")
            return {"blocked": False, "size": kwargs.get("hedge_size_override")}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            captured["size"] = size
            captured["trade_source"] = trade_source
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "calculate_size", _fake_calculate_size)
        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "sell"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert captured["hedge_size_override"] == pytest.approx(7.5)
        assert captured["size"] == pytest.approx(7.5)
        assert captured["trade_source"] == "hedge"

    def test_opposite_signal_against_manual_open_trade_is_not_marked_hedge(self, monkeypatch):
        import webhook

        monkeypatch.setattr(
            webhook,
            "load_raw_log",
            lambda: [{"ticker": "INTC", "side": "long", "status": "OPEN", "trade_source": "manual"}],
        )
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "sell"})
        monkeypatch.setattr(webhook.session, "request", lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 100.0, "offer": 100.2}}})())
        monkeypatch.setattr(webhook, "calculate_size", lambda **kwargs: {"blocked": False, "size": 1.0})
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        captured = {}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            captured["trade_source"] = trade_source
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "sell"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert captured["trade_source"] == "tradingview"

    def test_alert_sl_is_capped_and_tp_passes_through_unchanged(self, monkeypatch):
        """The alert's own SL/TP is used (the fixed SL/TP override is
        switched off): an overly wide SL is pulled in to MAX_SL_PERC_OF_EQUITY
        of equity used, while the alert's TP is forwarded untouched."""
        import webhook

        monkeypatch.setattr(webhook.config, "MAX_SL_PERC_OF_EQUITY", 0.20)
        monkeypatch.setattr(webhook.config, "LEVERAGE", 5)
        monkeypatch.setattr(webhook, "load_raw_log", lambda: [])
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(
            webhook,
            "parse_tradingview_alert",
            lambda payload: {"symbol": "INTC", "action": "buy", "sl": 50.0, "tp": 200.0, "timeframe": "5M"},
        )
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 99.8, "offer": 100.0}}})(),
        )
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)

        captured = {}

        def _fake_calculate_size(**kwargs):
            captured["sl_price"] = kwargs.get("sl_price")
            captured["tp_price"] = kwargs.get("tp_price")
            return {"blocked": False, "size": 1.0}

        def _fake_place_order(epic, action, size, sl, tp, timeframe=None, trade_source="tradingview"):
            captured["order_sl"] = sl
            captured["order_tp"] = tp
            return {"status": "ok"}

        monkeypatch.setattr(webhook, "calculate_size", _fake_calculate_size)
        monkeypatch.setattr(webhook, "place_order", _fake_place_order)

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "buy"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        # entry=100 (buy uses offer), cap = 20%/5 leverage = 4% -> 100*(1-0.04)=96.0
        assert captured["sl_price"] == pytest.approx(96.0)
        assert captured["tp_price"] == pytest.approx(200.0)
        assert captured["order_sl"] == pytest.approx(96.0)
        assert captured["order_tp"] == pytest.approx(200.0)

    def test_alert_missing_sl_tp_falls_back_to_fixed_sl_tp(self, monkeypatch):
        """When the alert doesn't supply its own sl/tp, the bot falls back
        to the fixed risk-percentage calculation so the trade is never
        placed without protection."""
        import webhook

        monkeypatch.setattr(webhook, "load_raw_log", lambda: [])
        monkeypatch.setattr(webhook.session, "verify_epic", lambda symbol: {"epic": "INTC", "source": "mock"})
        monkeypatch.setattr(webhook, "_is_duplicate_alert", lambda *_: False)
        monkeypatch.setattr(webhook, "_is_trade_locked_now", lambda: False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert", lambda payload: {"symbol": "INTC", "action": "buy"})
        monkeypatch.setattr(
            webhook.session,
            "request",
            lambda *args, **kwargs: type("Resp", (), {"status_code": 200, "json": lambda self: {"snapshot": {"bid": 99.8, "offer": 100.0}}})(),
        )
        monkeypatch.setattr(webhook.session, "update_last_trade", lambda: None)
        monkeypatch.setattr(webhook.FixedSLTP, "long_levels", staticmethod(lambda entry_price: (90.0, 140.0)))

        captured = {}

        def _fake_calculate_size(**kwargs):
            captured["sl_price"] = kwargs.get("sl_price")
            captured["tp_price"] = kwargs.get("tp_price")
            return {"blocked": False, "size": 1.0}

        monkeypatch.setattr(webhook, "calculate_size", _fake_calculate_size)
        monkeypatch.setattr(webhook, "place_order", lambda *a, **k: {"status": "ok"})

        client = webhook.app.test_client()
        resp = client.post("/webhook", json={"symbol": "INTC", "action": "buy"})
        body = resp.get_json() or {}

        assert resp.status_code == 200
        assert body.get("status") == "ok"
        assert captured["sl_price"] == pytest.approx(90.0)
        assert captured["tp_price"] == pytest.approx(140.0)


# ======================================================================== #
#  dashboard: safe analytics defaults                                       #
# ======================================================================== #

class TestProtectedRoutes:
    def test_debug_routes_disabled_by_default(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "DEBUG_ROUTES", False)
        client = webhook.app.test_client()

        assert client.get("/debug/tokens").status_code == 404
        assert client.get("/debug/order/X/BUY/1").status_code == 404
        assert client.get("/raw").status_code == 404

    def test_debug_route_requires_dashboard_auth(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "DEBUG_ROUTES", True)
        client = webhook.app.test_client()

        response = client.get("/debug/tokens")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard/login")

    def test_raw_route_requires_dashboard_auth(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "DEBUG_ROUTES", True)
        client = webhook.app.test_client()

        response = client.get("/raw")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard/login")


class TestWebhookSecret:
    def test_rejects_missing_or_wrong_secret(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
        client = webhook.app.test_client()

        assert client.post("/webhook", data="{}", content_type="application/json").status_code == 401
        assert client.post("/webhook", json={"secret": "nope"}).status_code == 401

    def test_accepts_secret_in_body_query_and_header(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
        client = webhook.app.test_client()

        assert client.post("/webhook", json={"secret": "s3cret"}).status_code != 401
        assert client.post("/webhook?secret=s3cret", json={}).status_code != 401
        assert client.post("/webhook", json={}, headers={"X-Webhook-Secret": "s3cret"}).status_code != 401

    def test_no_secret_configured_allows_requests(self, monkeypatch):
        import webhook
        monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
        client = webhook.app.test_client()

        assert client.post("/webhook", json={}).status_code != 401


class TestTickerOrderLock:
    """Tests for webhook._get_ticker_order_lock, which serializes the
    'check for an open trade -> place order' section per ticker so two
    near-simultaneous requests for the same ticker can't both race past the
    open-trade check and place two real orders at the broker."""

    def _webhook_module(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import webhook as wh
        return wh

    def test_same_ticker_returns_same_lock_case_insensitive(self):
        wh = self._webhook_module()
        lock1 = wh._get_ticker_order_lock("MRVL")
        lock2 = wh._get_ticker_order_lock("mrvl")
        assert lock1 is lock2

    def test_different_tickers_get_different_locks(self):
        wh = self._webhook_module()
        lock1 = wh._get_ticker_order_lock("MRVL")
        lock2 = wh._get_ticker_order_lock("NFLX")
        assert lock1 is not lock2

    def test_second_concurrent_acquire_for_same_ticker_fails_fast(self):
        wh = self._webhook_module()
        lock = wh._get_ticker_order_lock("DUPTEST")
        assert lock.acquire(blocking=False) is True
        try:
            # Simulates a second near-simultaneous webhook request for the
            # same ticker while the first is still mid-flight placing an order.
            assert lock.acquire(blocking=False) is False
        finally:
            lock.release()
        # Lock is free again once released, so a later, non-overlapping
        # request for the same ticker can proceed normally.
        assert lock.acquire(blocking=False) is True
        lock.release()


class TestTradingViewAlertParserTimeframe:
    """Regression tests for bare (non 'TF:'-prefixed) timeframe tokens, e.g.
    the Alert Helper format "BUY|BE|15M|SL:284.39|TP:298.04", which were
    previously silently dropped since the parser only recognised an explicit
    "TF:"/"TF=" prefix or a space-separated "TF value" pair.
    """

    def test_bare_timeframe_token_is_parsed_from_raw_alert(self):
        from parser import parse_tradingview_alert
        result = parse_tradingview_alert("BUY|BE|15M|SL:284.3892857143|TP:298.0421428571")
        assert result["blocked"] is False
        assert result["timeframe"] == "15M"
        assert result["symbol"] == "BE"
        assert result["action"] == "buy"

    def test_bare_hourly_timeframe_token_is_parsed(self):
        from parser import parse_tradingview_alert
        result = parse_tradingview_alert("SELL|NVDA|1H|SL:120|TP:110")
        assert result["blocked"] is False
        assert result["timeframe"] == "1H"

    def test_prefixed_tf_token_still_parses(self):
        """Explicit 'TF:' prefix must keep working alongside the bare form."""
        from parser import parse_tradingview_alert
        result = parse_tradingview_alert("BUY|NVDA|SL:120|TP:130|TF:30M")
        assert result["blocked"] is False
        assert result["timeframe"] == "30M"

    def test_missing_timeframe_token_still_parses_without_one(self):
        from parser import parse_tradingview_alert
        result = parse_tradingview_alert("BUY|NVDA|SL:120|TP:130")
        assert result["blocked"] is False
        assert result["timeframe"] is None


class TestWeeklyGate:
    @staticmethod
    def _trades():
        def mk(stock, pnls):
            return [{"ticker": stock, "trade_type": "tradingview", "status": "CLOSED", "pnl_gbp": p} for p in pnls]
        return (
            mk("AAA", [10, 12, 9, 11, 10])
            + mk("BBB", [5, -1, 4, 6, 3])
            + mk("CCC", [8, -2, 7, 6, 5])
            + mk("DDD", [6, -3, 5, 4, 7])
            + mk("EEE", [-4, 5, -6, 3, -2])
            + mk("NEW", [-5, -5])
        )

    def test_negative_week_rejects_weak_stock_only(self):
        import gating
        t = self._trades()
        assert gating.evaluate("EEE", weekly_return=-1.0, trades=t)["action"] == "reject"
        assert gating.evaluate("AAA", weekly_return=-1.0, trades=t)["action"] == "allow"
        assert gating.evaluate("NEW", weekly_return=-1.0, trades=t)["action"] == "allow"

    def test_profit_bands(self):
        import gating
        t = self._trades()
        assert gating.evaluate("EEE", weekly_return=3.0, trades=t)["action"] == "allow"
        assert gating.evaluate("EEE", weekly_return=7.0, trades=t)["action"] == "reject"
        r = gating.evaluate("EEE", weekly_return=12.0, trades=t)
        assert r["action"] == "reduce" and r["scale"] == 0.5

    def test_rejected_log_round_trip(self, tmp_path):
        import gating
        p = str(tmp_path / "rej.json")
        assert gating.record_rejected({"stock": "EEE", "tv_sl": 1.0, "tv_tp": 2.0}, path=p)
        rows = gating.load_rejected(p)
        assert rows[0]["tv_sl"] == 1.0 and rows[0]["time_rejected"]

    def test_size_scale_reduces_order(self, monkeypatch):
        import sizing
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 100)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 300)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 500)
        monkeypatch.setattr(sizing.session, "get_account", lambda: {"available": 5000})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda a: a)
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])
        full = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")
        half = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA", size_scale=0.5)
        assert full["size"] == 5.0 and half["size"] == 2.5


class TestTradingSwitch:
    def test_defaults_on_and_persists(self, tmp_path, monkeypatch):
        import trading_switch
        monkeypatch.setattr(trading_switch.config, "TRADING_SWITCH_PATH", str(tmp_path / "s.json"), raising=False)
        assert trading_switch.is_enabled() is True
        assert trading_switch.set_enabled(False)
        assert trading_switch.is_enabled() is False
        assert trading_switch.set_enabled(True)
        assert trading_switch.is_enabled() is True

    def test_webhook_rejects_when_off_and_logs_to_rejected(self, tmp_path, monkeypatch):
        import webhook, gating, trading_switch
        monkeypatch.setattr(trading_switch.config, "TRADING_SWITCH_PATH", str(tmp_path / "s.json"), raising=False)
        monkeypatch.setattr(gating.config, "REJECTED_TRADES_PATH", str(tmp_path / "rej.json"), raising=False)
        trading_switch.set_enabled(False)
        monkeypatch.setattr(webhook, "parse_tradingview_alert",
                            lambda p: {"symbol": "INTC", "action": "buy", "sl": 90.0, "tp": 120.0, "timeframe": "15m"})
        placed = []
        monkeypatch.setattr(webhook, "place_order", lambda *a, **k: placed.append(1) or {"status": "ok"})
        resp = webhook.app.test_client().post("/webhook", json={"symbol": "INTC", "action": "buy"})
        assert (resp.get_json() or {}).get("reason") == "trading_off"
        assert placed == []
        rows = gating.load_rejected()
        assert rows and rows[0]["tv_sl"] == 90.0 and rows[0]["tv_tp"] == 120.0

    def test_time_lock_off_by_default(self):
        import config
        assert config.TRADE_LOCK_ENABLED is False
