"""Tests for dashboard: analytics, roles, endpoints."""

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


class TestSafeAnalytics:
    """Tests for dashboard._safe_analytics."""

    def test_none_input_returns_defaults(self):
        from dashboard import _safe_analytics
        result = _safe_analytics(None)
        assert result["trade_count"] == 0
        assert "win_rate" in result

    def test_partial_dict_gets_missing_keys(self):
        from dashboard import _safe_analytics
        result = _safe_analytics({"win_rate": 60})
        assert result["win_rate"] == 60
        assert result["trade_count"] == 0  # was missing, got default

    def test_all_keys_present(self):
        from dashboard import _safe_analytics
        result = _safe_analytics({})
        expected_keys = {"win_rate", "avg_win", "avg_loss", "expectancy", "total_pl", "max_drawdown", "trade_count", "story"}
        assert expected_keys.issubset(result.keys())

    def test_existing_values_preserved(self):
        from dashboard import _safe_analytics
        inp = {"win_rate": 75.5, "trade_count": 20, "total_pl": 1000.0}
        result = _safe_analytics(inp)
        assert result["win_rate"] == 75.5
        assert result["trade_count"] == 20
        assert result["total_pl"] == 1000.0


class TestDashboardDedupe:
    def test_keeps_distinct_closed_trades_that_reuse_same_dealid(self):
        from dashboard import dedupe_trades
        trades = [
            {
                "dealId": "REUSED",
                "ticker": "STX",
                "side": "short",
                "entry_price": 790.25,
                "exit_price": 791.71,
                "time_entered": "2026-09-01T10:00:00Z",
                "time_exited": "2026-09-01T12:00:00Z",
                "status": "CLOSED",
            },
            {
                "dealId": "REUSED",
                "ticker": "STX",
                "side": "short",
                "entry_price": 790.25,
                "exit_price": 788.10,
                "time_entered": "2026-09-08T10:00:00Z",
                "time_exited": "2026-09-08T12:00:00Z",
                "status": "CLOSED",
            },
        ]
        assert len(dedupe_trades(trades)) == 2

    def test_normalize_trades_maps_trade_type_labels(self):
        from dashboard import normalize_trades
        trades = normalize_trades([
            {"trade_source": "webhook"},
            {"trade_source": "bot"},
            {"trade_source": "manual"},
            {"trade_source": "trader"},
            {"trade_source": "hedge"},
            {"notes": "Imported from webhook (legacy)"},
            {"trade_source": "unknown"},
            {},
        ])
        assert [t["trade_type"] for t in trades] == [
            "TradingView",
            "TradingView",
            "Trader",
            "Trader",
            "Hedge",
            "TradingView",
            "Trader",
            "Trader",
        ]

    def test_request_context_keeps_open_trades_in_trade_log(self, monkeypatch):
        import dashboard

        monkeypatch.setattr(dashboard.session, "get_positions", lambda fresh=False: [])
        monkeypatch.setattr(dashboard.session, "get_account", lambda: {})
        monkeypatch.setattr(dashboard.session, "enrich_positions", lambda raw: [])
        monkeypatch.setattr(dashboard.session, "enrich_account", lambda raw: {})
        monkeypatch.setattr(dashboard, "reconcile_with_positions", lambda positions: {"closed": [], "added": [], "reopened": []})
        monkeypatch.setattr(dashboard, "load_raw_log", lambda: [
            {
                "dealId": "OPEN1",
                "ticker": "STX",
                "side": "long",
                "size": 1.2,
                "entry_price": 790.25,
                "time_entered": "2026-09-09T10:00:00Z",
                "status": "OPEN",
                "trade_source": "trader",
            },
            {
                "dealId": "CLOSED1",
                "ticker": "NVDA",
                "side": "short",
                "size": 1.0,
                "entry_price": 200.0,
                "exit_price": 195.0,
                "time_entered": "2026-09-08T10:00:00Z",
                "time_exited": "2026-09-08T12:00:00Z",
                "status": "CLOSED",
                "pnl_gbp": 5.0,
                "trade_source": "tradingview",
            },
        ])

        ctx = dashboard._build_request_context()

        assert len(ctx["combined_trades"]) == 2
        assert any(t["status"] == "OPEN" and t["trade_type"] == "Trader" for t in ctx["combined_trades"])
        assert any(t["status"] == "CLOSED" and t["trade_type"] == "TradingView" for t in ctx["combined_trades"])
        assert ctx["analytics"]["trade_count"] == 1

    def test_dashboard_header_has_live_uk_clock_and_single_trade_log(self, monkeypatch):
        from flask import Flask
        import dashboard

        monkeypatch.setattr(dashboard, "_build_request_context", lambda: {
            "account": {"equity": 1000, "balance": 900, "pnl": 100, "available": 800},
            "positions": [],
            "combined_trades": [],
            "analytics": dashboard._safe_analytics({}),
            "weekly_analytics": dashboard._safe_analytics({}),
            "monthly_analytics": dashboard._safe_analytics({}),
        })

        app = Flask(__name__)
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        html = client.get("/dashboard").get_data(as_text=True)

        assert 'id="uk-clock-value"' in html
        assert "Europe/London" in html
        assert html.count(">Trade Log<") == 1


class TestComputeAnalytics:
    """Tests for dashboard.compute_analytics."""

    def test_empty_trades_returns_safe_dict(self):
        from dashboard import compute_analytics
        result = compute_analytics([])
        assert result["trade_count"] == 0
        assert result["win_rate"] is None

    def test_single_winning_trade(self):
        from dashboard import compute_analytics
        trades = [{"status": "CLOSED", "pnl": 100.0, "pnl_gbp": 100.0}]
        result = compute_analytics(trades)
        assert result["trade_count"] == 1
        assert result["win_rate"] == 100.0
        assert result["total_pl"] == pytest.approx(100.0)

    def test_mixed_trades(self):
        from dashboard import compute_analytics
        trades = [
            {"status": "CLOSED", "pnl": 100.0, "pnl_gbp": 100.0},
            {"status": "CLOSED", "pnl": -50.0, "pnl_gbp": -50.0},
            {"status": "CLOSED", "pnl": 200.0, "pnl_gbp": 200.0},
        ]
        result = compute_analytics(trades)
        assert result["trade_count"] == 3
        assert result["win_rate"] == pytest.approx(100 * 2 / 3, rel=1e-3)
        assert result["total_pl"] == pytest.approx(250.0)

    def test_open_trades_excluded_from_analytics(self):
        from dashboard import compute_analytics, filter_completed
        trades = [
            {"status": "OPEN", "pnl": None},
            {"status": "CLOSED", "pnl": 50.0, "pnl_gbp": 50.0},
        ]
        result = compute_analytics(filter_completed(trades))
        assert result["trade_count"] == 1

    def test_analytics_use_gbp_pnl_when_only_usd_pnl_present(self):
        """When pnl_gbp is missing, pnl should be converted using config.FX_USD_GBP."""
        from dashboard import compute_analytics
        import config
        trades = [{"status": "CLOSED", "pnl": 100.0}]
        result = compute_analytics(trades)
        assert result["total_pl"] == pytest.approx(round(100.0 * config.FX_USD_GBP, 2))


class TestAnalyticsMonthFilter:
    """Tests for dashboard's per-month Analytics filter helpers and the
    /dashboard/analytics?month=YYYY-MM route."""

    def test_available_analytics_months_lists_distinct_months_newest_first(self):
        from dashboard import _available_analytics_months
        trades = [
            {"time_exited": "2026-09-15T10:00:00"},
            {"time_exited": "2026-10-02T10:00:00"},
            {"time_exited": "2026-09-20T10:00:00"},
            {"time_entered": "2026-08-01T10:00:00"},  # no time_exited: falls back
        ]
        assert _available_analytics_months(trades) == ["2026-10", "2026-09", "2026-08"]

    def test_trades_in_month_filters_by_exit_time(self):
        from dashboard import _trades_in_month
        trades = [
            {"time_exited": "2026-09-15T10:00:00", "pnl": 1},
            {"time_exited": "2026-10-02T10:00:00", "pnl": 2},
        ]
        result = _trades_in_month(trades, "2026-09")
        assert len(result) == 1
        assert result[0]["pnl"] == 1

    def test_trades_in_month_falls_back_to_entry_time_when_no_exit(self):
        from dashboard import _trades_in_month
        trades = [{"time_entered": "2026-09-15T10:00:00", "pnl": 1}]
        assert len(_trades_in_month(trades, "2026-09")) == 1
        assert len(_trades_in_month(trades, "2026-10")) == 0

    def test_analytics_route_filters_to_selected_month(self, monkeypatch):
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        trades = [
            {"status": "CLOSED", "pnl": 100.0, "pnl_gbp": 100.0, "time_exited": "2026-09-15T10:00:00"},
            {"status": "CLOSED", "pnl": -40.0, "pnl_gbp": -40.0, "time_exited": "2026-10-02T10:00:00"},
        ]
        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"balance": 1000, "pnl": 0},
            "combined_trades": trades,
            "analytics": dashboard_module._safe_analytics(dashboard_module.compute_analytics(trades)),
        })

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/analytics?month=2026-09")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "2026-09" in body

    def test_analytics_route_ignores_unknown_month(self, monkeypatch):
        """An unrecognised ?month= value falls back to all-time rather than
        erroring or silently returning zero trades."""
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        trades = [{"status": "CLOSED", "pnl": 100.0, "pnl_gbp": 100.0, "time_exited": "2026-09-15T10:00:00"}]
        analytics = dashboard_module._safe_analytics(dashboard_module.compute_analytics(trades))
        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"balance": 1000, "pnl": 0},
            "combined_trades": trades,
            "analytics": analytics,
        })

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/analytics?month=2099-01")

        assert response.status_code == 200


class TestDashboardRoles:
    def _viewer_client(self, monkeypatch, with_cookie=False):
        from flask import Flask
        import dashboard

        monkeypatch.setattr(dashboard, "_build_request_context", lambda: {
            "account": {"equity": 1234, "balance": 987, "pnl": -316.79, "available": 55},
            "positions": [{"ticker": "AAPL", "direction": "Long", "entry_price": 150.5,
                           "size": 7, "current_price": 140, "profit": -316.79, "dealId": "D1"}],
            "combined_trades": [],
            "analytics": dashboard._safe_analytics({}),
            "weekly_analytics": dashboard._safe_analytics({}),
            "monthly_analytics": dashboard._safe_analytics({}),
        })
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        if with_cookie:
            client.set_cookie("dashboard_auth", "1")
            client.set_cookie("dashboard_role", "viewer")
        return client

    def test_anonymous_visitor_is_viewer_with_limited_positions(self, monkeypatch):
        client = self._viewer_client(monkeypatch)
        html = client.get("/dashboard").get_data(as_text=True)
        assert "AAPL" in html and "150.5" not in html and ">Entry<" not in html and ">Size<" not in html
        assert "316.79" not in html and "1234" not in html and "987" not in html
        assert ">Size<" not in html and ">SL / TP<" not in html
        assert ">Trade Log<" not in html and ">Analytics<" not in html
        assert ">Login<" in html and ">Logout<" not in html

    def test_viewer_data_endpoint_hides_account_and_position_details(self, monkeypatch):
        client = self._viewer_client(monkeypatch)
        data = client.get("/dashboard/data").get_json()
        assert data["account"] == {}
        assert data["positions"] == [{"ticker": "AAPL", "direction": "Long"}]

    def test_viewer_cannot_open_trade_log_or_analytics(self, monkeypatch):
        client = self._viewer_client(monkeypatch)
        for path in ("/dashboard/trades", "/dashboard/analytics"):
            resp = client.get(path)
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/dashboard")
        assert client.get("/dashboard/trades/data").status_code == 403

    def test_investors_list_hidden_from_viewer(self, monkeypatch):
        client = self._viewer_client(monkeypatch)
        monkeypatch.setattr("deposits.list_investors", lambda entries: ["Secret Investor"])
        html = client.get("/dashboard/investors").get_data(as_text=True)
        assert "Secret Investor" not in html

    def test_logged_in_user_sees_logout_and_logout_returns_to_dashboard(self, monkeypatch):
        client = self._viewer_client(monkeypatch, with_cookie=True)
        assert ">Logout<" in client.get("/dashboard").get_data(as_text=True)
        resp = client.get("/dashboard/logout")
        assert resp.headers["Location"].endswith("/dashboard/login")

    def test_viewer_role_is_blocked_from_close_endpoint(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.close_live_position", lambda position_id: {"status": "success"})

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.post("/dashboard/close/D1")
        data = response.get_json()

        assert response.status_code == 403
        assert data["message"] == "forbidden_viewer_role"

    def test_viewer_role_is_blocked_from_delete_endpoint(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.delete_trade_log_entry", lambda idx: (True, {}, "ok"))

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.post("/dashboard/trade/0/delete")
        data = response.get_json()

        assert response.status_code == 403
        assert data["message"] == "forbidden_viewer_role"

    def test_viewer_role_is_blocked_from_update_type_endpoint(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.update_trade_type_entry", lambda idx, new_type: (True, {}, "ok"))

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.post("/dashboard/trade/0/type", json={"type": "hedge"})
        data = response.get_json()

        assert response.status_code == 403
        assert data["message"] == "forbidden_viewer_role"

    def test_viewer_role_is_blocked_from_update_exit_price_endpoint(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.update_trade_exit_price_entry", lambda idx, new_exit_price: (True, {}, "ok"))

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.post("/dashboard/trade/0/exit_price", json={"exit_price": 110})
        data = response.get_json()

        assert response.status_code == 403
        assert data["message"] == "forbidden_viewer_role"

    def test_owner_role_can_update_exit_price(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr(
            "dashboard.update_trade_exit_price_entry",
            lambda idx, new_exit_price: (True, {"exit_price": float(new_exit_price)}, "updated"),
        )

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "owner")

        response = client.post("/dashboard/trade/0/exit_price", json={"exit_price": 110})
        data = response.get_json()

        assert response.status_code == 200
        assert data["status"] == "success"

    def test_owner_role_can_still_use_close_endpoint(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.close_live_position", lambda position_id: {"status": "success"})

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "owner")

        response = client.post("/dashboard/close/D1")

        assert response.status_code == 200

    def test_legacy_auth_cookie_without_role_is_treated_as_owner(self, monkeypatch):
        """Pre-existing sessions created before roles existed only carry
        dashboard_auth; they must keep full (Owner) access."""
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.close_live_position", lambda position_id: {"status": "success"})

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.post("/dashboard/close/D1")

        assert response.status_code == 200

    def test_dashboard_home_marks_viewer_role_in_context(self, monkeypatch):
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"pnl": 0},
            "positions": [],
            "combined_trades": [],
            "analytics": {
                "win_rate": 0, "expectancy": 0, "trade_count": 0,
                "total_pl": 0, "max_drawdown": 0,
            },
            "weekly_analytics": dashboard_module._safe_analytics({}),
            "monthly_analytics": dashboard_module._safe_analytics({}),
        })

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.get("/dashboard")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Viewer" in body
        assert "button onclick=\"closePosition" not in body

    def test_dashboard_home_renders_period_returns_card(self, monkeypatch):
        """The Return card should show the Daily/Weekly/Monthly % figures
        computed from calendar-aligned balance history, when available."""
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"pnl": 0},
            "positions": [],
            "combined_trades": [],
            "analytics": {
                "win_rate": 0, "expectancy": 0, "trade_count": 0,
                "total_pl": 0, "max_drawdown": 0,
            },
            "weekly_analytics": dashboard_module._safe_analytics({}),
            "monthly_analytics": dashboard_module._safe_analytics({}),
            "period_returns": {
                "daily": 1.5, "weekly": -2.25, "monthly": 10.0,
                "daily_opening": 1000.0, "weekly_opening": 990.0, "monthly_opening": 900.0,
                "goal": {"goal_pct": 5.0, "progress_pct": 0.0},
                "successful_weeks": {"successful_weeks": 3, "total_weeks": 8, "avg_weekly_return": 2.5},
            },
        })

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "1.5%" in body
        assert "-2.25%" in body
        assert "10.0%" in body
        assert "3/8" in body
        assert "Successful Weeks" in body
        assert "2.5%" in body
        assert "Terms of Service" in body
        assert "Contact Us" in body
        assert "FAQ" in body
        assert "Weekly Goal: 5.0%" in body

    def test_investor_role_is_blocked_from_close_endpoint(self, monkeypatch):
        """Investor has the same read-only restrictions as Viewer."""
        from flask import Flask
        from dashboard import dashboard

        monkeypatch.setattr("dashboard.close_live_position", lambda position_id: {"status": "success"})

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "investor")

        response = client.post("/dashboard/close/D1")
        data = response.get_json()

        assert response.status_code == 403
        assert data["message"] == "forbidden_viewer_role"

    def test_investor_role_is_blocked_from_setting_investor_tier(self, monkeypatch):
        """Only the Owner can change an investor's ROI tier."""
        from flask import Flask
        from dashboard import dashboard

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "investor")

        response = client.post("/dashboard/investors/tier", data={"investor": "Carol", "tier": "full"})

        assert response.status_code == 403

    def test_investor_role_can_view_roi_page(self, monkeypatch):
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"balance": 1000, "pnl": 0, "equity": 1000, "available": 1000},
            "combined_trades": [],
        })
        monkeypatch.setattr(dashboard_module.deposits, "list_entries_sorted", lambda: [])

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "investor")

        response = client.get("/dashboard/roi")

        assert response.status_code == 200

    def test_viewer_role_is_redirected_away_from_roi_page(self, monkeypatch):
        """Viewer accounts cannot see per-investor gain/loss figures."""
        from flask import Flask
        from dashboard import dashboard

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")

        response = client.get("/dashboard/roi")

        assert response.status_code == 302
        assert response.headers["Location"] == "/dashboard"

    def test_dashboard_home_marks_investor_role_badge_in_context(self, monkeypatch):
        from flask import Flask
        import dashboard as dashboard_module
        from dashboard import dashboard

        monkeypatch.setattr(dashboard_module, "_build_request_context", lambda: {
            "account": {"pnl": 0},
            "positions": [],
            "combined_trades": [],
            "analytics": {
                "win_rate": 0, "expectancy": 0, "trade_count": 0,
                "total_pl": 0, "max_drawdown": 0,
            },
            "weekly_analytics": dashboard_module._safe_analytics({}),
            "monthly_analytics": dashboard_module._safe_analytics({}),
        })

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "investor")

        response = client.get("/dashboard")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Investor" in body
        assert 'href="/dashboard/roi"' in body
        assert 'href="/dashboard/transactions"' not in body


class TestDashboardCloseEndpoint:
    def test_close_endpoint_calls_close_service(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        called = {}

        def _fake_close(position_id):
            called["position_id"] = position_id
            return {"status": "success", "message": f"Position {position_id} closed."}

        monkeypatch.setattr("dashboard.close_live_position", _fake_close)

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.post("/dashboard/close/D1")
        data = response.get_json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert called["position_id"] == "D1"

    def test_close_endpoint_bubbles_service_error(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        def _fake_close(_position_id):
            return {"status": "error", "message": "broker_close_failed_400"}

        monkeypatch.setattr("dashboard.close_live_position", _fake_close)

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.post("/dashboard/close/D1")
        data = response.get_json()

        assert response.status_code == 502
        assert data["status"] == "error"


class TestDashboardDeleteTradeEndpoint:
    def test_delete_endpoint_calls_trade_log_service(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        called = {}

        def _fake_delete(trade_index):
            called["trade_index"] = trade_index
            return True, {"dealId": "D1"}, "deleted"

        monkeypatch.setattr("dashboard.delete_trade_log_entry", _fake_delete)

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.post("/dashboard/trade/4/delete")
        data = response.get_json()

        assert response.status_code == 200
        assert data["status"] == "success"
        assert called["trade_index"] == 4

    def test_dashboard_data_renders_delete_button_only_for_completed_trades(self, monkeypatch):
        from flask import Flask
        import dashboard

        live_positions = [{
            "dealId": "LIVE2",
            "ticker": "NVDA",
            "direction": "Long",
            "size": 1.0,
            "entry_price": 100.0,
            "current_price": 101.0,
            "stopLevel": None,
            "limitLevel": None,
            "profit": 1.0,
        }]
        monkeypatch.setattr(dashboard.session, "get_positions", lambda fresh=False: live_positions)
        monkeypatch.setattr(dashboard.session, "get_account", lambda: {})
        monkeypatch.setattr(dashboard.session, "enrich_positions", lambda raw: live_positions)
        monkeypatch.setattr(dashboard.session, "enrich_account", lambda raw: {})
        monkeypatch.setattr(dashboard, "reconcile_with_positions", lambda positions: {"closed": [], "added": [], "reopened": []})
        monkeypatch.setattr(dashboard, "load_raw_log", lambda: [
            {"dealId": "OPEN1", "ticker": "AAPL", "status": "OPEN", "trade_source": "trader", "pnl_gbp": None},
            {"dealId": "PHANTOM1", "dealReference": "REF-PHANTOM1", "ticker": "MSFT", "status": "OPEN", "trade_source": "tradingview", "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-PHANTOM1", "pnl_gbp": None},
            {"dealId": "CLOSED1", "ticker": "NVDA", "status": "CLOSED", "time_exited": "2026-09-09T12:00:00Z", "pnl_gbp": 5.0},
        ])

        app = Flask(__name__)
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/trades/data")
        data = response.get_json()
        html = data["html"]

        assert response.status_code == 200
        assert "deleteTrade(2)" in html
        assert "deleteTrade(1)" in html
        assert "deleteTrade(0)" not in html

    def test_dashboard_data_hides_open_delete_when_live_snapshot_unavailable(self, monkeypatch):
        from flask import Flask
        import dashboard

        monkeypatch.setattr(dashboard.session, "get_positions", lambda fresh=False: None)
        monkeypatch.setattr(dashboard.session, "get_account", lambda: {})
        monkeypatch.setattr(dashboard.session, "enrich_positions", lambda raw: [])
        monkeypatch.setattr(dashboard.session, "enrich_account", lambda raw: {})
        monkeypatch.setattr(dashboard, "reconcile_with_positions", lambda positions: {"closed": [], "added": [], "reopened": []})
        monkeypatch.setattr(dashboard, "load_raw_log", lambda: [
            {"dealId": "PHANTOM1", "dealReference": "REF-PHANTOM1", "ticker": "MSFT", "status": "OPEN", "trade_source": "tradingview", "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-PHANTOM1", "pnl_gbp": None},
        ])

        app = Flask(__name__)
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/trades/data")
        data = response.get_json()
        html = data["html"]

        assert response.status_code == 200
        assert "deleteTrade(0)" not in html

    def test_dashboard_data_hides_open_delete_when_snapshot_has_no_extractable_deal_ids(self, monkeypatch):
        from flask import Flask
        import dashboard

        monkeypatch.setattr(dashboard.session, "get_positions", lambda fresh=False: [{"market": {"symbol": "MSFT"}}])
        monkeypatch.setattr(dashboard.session, "get_account", lambda: {})
        monkeypatch.setattr(dashboard.session, "enrich_positions", lambda raw: [{
            "ticker": "MSFT",
            "direction": "Long",
            "size": 1.0,
            "entry_price": 100.0,
            "current_price": 101.0,
            "stopLevel": None,
            "limitLevel": None,
            "profit": 1.0,
        }])
        monkeypatch.setattr(dashboard.session, "enrich_account", lambda raw: {})
        monkeypatch.setattr(dashboard, "reconcile_with_positions", lambda positions: {"closed": [], "added": [], "reopened": []})
        monkeypatch.setattr(dashboard, "load_raw_log", lambda: [
            {"dealId": "PHANTOM1", "dealReference": "REF-PHANTOM1", "ticker": "MSFT", "status": "OPEN", "trade_source": "tradingview", "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-PHANTOM1", "pnl_gbp": None},
        ])

        app = Flask(__name__)
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/trades/data")
        data = response.get_json()
        html = data["html"]

        assert response.status_code == 200
        assert "deleteTrade(0)" not in html


# ======================================================================== #
#  webhook: per-ticker order lock (prevents duplicate real broker orders)  #
# ======================================================================== #
