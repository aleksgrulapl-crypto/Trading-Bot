#!/usr/bin/env python3
"""
test_critical_paths.py

Unit tests for critical / high-priority functions:
  - trade_log: thread safety, validation, locking
  - webhook: payload validation
  - dashboard: safe analytics defaults
"""

import json
import os
import tempfile
import threading
import time
import sys
import pytest

# ------------------------------------------------------------------ helpers --

def _make_tmp_log(initial_trades=None):
    """Create a temp JSON file holding *initial_trades* (default: [])."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(initial_trades or [], f)
    return path


# ======================================================================== #
#  trade_log                                                                #
# ======================================================================== #

class TestUpsertOpenTrade:
    """Tests for trade_log.upsert_open_trade validation."""

    def test_rejects_missing_entry_price(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 10}, path=path)
        assert result is None, "Should reject when entry_price is missing"

    def test_rejects_zero_entry_price(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 10, "entry_price": 0}, path=path)
        assert result is None, "Should reject when entry_price is zero"

    def test_rejects_negative_entry_price(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 10, "entry_price": -5}, path=path)
        assert result is None, "Should reject when entry_price is negative"

    def test_rejects_zero_size(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 0, "entry_price": 100}, path=path)
        assert result is None, "Should reject when size is zero"

    def test_rejects_invalid_side(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 10, "entry_price": 100, "side": "INVALID"}, path=path)
        assert result is None, "Should reject unrecognised side string"

    def test_accepts_valid_trade(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = upsert_open_trade({"ticker": "NVDA", "size": 10, "entry_price": 130.5, "side": "buy"}, path=path)
        assert result is not None
        assert result["ticker"] == "NVDA"
        assert result["entry_price"] == 130.5
        assert result["status"] == "OPEN"

    def test_accepts_all_valid_sides(self, tmp_path):
        from trade_log import upsert_open_trade
        for i, side in enumerate(("buy", "sell", "long", "short")):
            path = str(tmp_path / f"log_{i}.json")
            with open(path, "w") as f:
                json.dump([], f)
            result = upsert_open_trade(
                {"ticker": "NVDA", "size": 10, "entry_price": 100 + i, "side": side},
                path=path,
            )
            assert result is not None, f"Side '{side}' should be accepted"

    def test_idempotent_on_same_dealId(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        payload = {"dealId": "D1", "ticker": "NVDA", "size": 10, "entry_price": 130.5}
        r1 = upsert_open_trade(payload, path=path)
        r2 = upsert_open_trade(payload, path=path)
        trades = load_raw_log(path)
        assert r1 is not None
        assert r2 is not None
        assert len(trades) == 1, "Second upsert of same dealId must not create duplicate"

    def test_broker_confirmed_dealid_backfills_pending_trade_not_duplicate(self, tmp_path):
        """A pending (dealId-less) trade recorded at order time should be matched and
        corrected by ticker when the broker-confirmed dealId/size/entry_price arrive
        slightly different (slippage/fill rounding), instead of creating a duplicate
        open-position entry that never gets closed."""
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": None, "dealReference": "ref123", "ticker": "MRVL", "side": "buy",
             "size": 6.8, "entry_price": 100.00},
            path=path,
        )
        upsert_open_trade(
            {"dealId": "D999", "dealReference": None, "ticker": "MRVL", "side": "buy",
             "size": 6.83, "entry_price": 100.02},
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1, "Broker-confirmed position must not create a duplicate entry"
        assert trades[0]["dealId"] == "D999"
        assert trades[0]["size"] == pytest.approx(6.83)
        assert trades[0]["entry_price"] == pytest.approx(100.02)


class TestReconcileWithPositions:
    """Tests for trade_log.reconcile_with_positions duplicate-prevention."""

    def test_live_position_backfills_pending_trade_instead_of_duplicating(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log, close_trade_by_dealId
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": None, "dealReference": "ref123", "ticker": "MRVL", "side": "buy",
             "size": 6.8, "entry_price": 100.00},
            path=path,
        )

        live_positions = [{
            "dealId": "D999", "dealReference": None, "ticker": "MRVL", "side": "buy",
            "size": 6.83, "entry_price": 100.02,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Reconcile must not create a duplicate entry for the same real position"
        assert not result["added"], "No new entry should be added when a pending trade is backfilled"
        assert trades[0]["dealId"] == "D999"
        assert trades[0]["size"] == pytest.approx(6.83)

        # Closing by the real dealId must close the single entry cleanly (no orphan left OPEN).
        closed = close_trade_by_dealId("D999", exit_price=105.0, path=path)
        assert closed is not None
        assert closed["status"] == "CLOSED"
        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["status"] == "CLOSED"

    def test_live_position_with_mismatched_dealid_merges_into_existing_open_trade(self, tmp_path):
        """A live position reported under a dealId that doesn't match any known
        dealId or dealId-less pending trade must still be merged into the
        existing OPEN trade for that ticker/side rather than logged as a
        second, duplicate entry (the bug shown on the dashboard where a
        completed 'self-closed' trade appears alongside the real open
        position for the same ticker)."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        # Existing OPEN trade already carries a dealId (e.g. reconciled earlier).
        upsert_open_trade(
            {"dealId": "D-OLD", "dealReference": "refA", "ticker": "MRVL", "side": "sell",
             "size": 4.8, "entry_price": 204.95},
            path=path,
        )

        # Broker reports the same ticker/side under a different dealId.
        live_positions = [{
            "dealId": "D-NEW", "dealReference": None, "ticker": "MRVL", "side": "sell",
            "size": 4.88, "entry_price": 204.95,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Reconcile must not create a duplicate entry for the same ticker/side"
        assert not result["added"]

    def test_live_position_for_different_ticker_still_added(self, tmp_path):
        """Sanity check: the widened dedup match must not swallow genuinely
        different positions for other tickers."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": "D-OLD", "dealReference": "refA", "ticker": "MRVL", "side": "sell",
             "size": 4.8, "entry_price": 204.95},
            path=path,
        )

        live_positions = [{
            "dealId": "D-OTHER", "dealReference": None, "ticker": "NFLX", "side": "sell",
            "size": 50.0, "entry_price": 82.62,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2
        assert result["added"]


class TestCloseTradeByDealId:
    """Tests for trade_log.close_trade_by_dealId."""

    def test_closes_open_trade(self, tmp_path):
        from trade_log import upsert_open_trade, close_trade_by_dealId, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        upsert_open_trade({"dealId": "D1", "ticker": "T", "size": 10, "entry_price": 100, "side": "buy"}, path=path)
        updated = close_trade_by_dealId("D1", exit_price=120, path=path)
        assert updated is not None
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 120.0
        assert updated["pnl"] == pytest.approx(200.0)

    def test_returns_none_for_missing_dealId(self, tmp_path):
        from trade_log import close_trade_by_dealId
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        result = close_trade_by_dealId("NONEXISTENT", exit_price=100, path=path)
        assert result is None

    def test_does_not_reclose_already_closed(self, tmp_path):
        from trade_log import upsert_open_trade, close_trade_by_dealId
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        upsert_open_trade({"dealId": "D1", "ticker": "T", "size": 5, "entry_price": 100, "side": "buy"}, path=path)
        close_trade_by_dealId("D1", exit_price=110, path=path)
        # Try to close again – should return None (already closed)
        result = close_trade_by_dealId("D1", exit_price=120, path=path)
        assert result is None, "Should not re-close an already closed trade"

    def test_closes_all_rows_sharing_a_duplicate_dealId(self, tmp_path):
        """Defensive fix: if a duplicate row for the same real position ever
        slips into the log sharing a dealId (e.g. the reconcile ticker-
        mismatch bug), closing that dealId must close *every* OPEN row
        carrying it instead of leaving the second one stuck OPEN forever
        (previously guaranteed by closed_ids/_last_close_cache in
        sync_closed_trades(), which are keyed by dealId, not by row)."""
        from trade_log import upsert_open_trade, close_trade_by_dealId, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)
        upsert_open_trade({"dealId": "DUP", "ticker": "STX", "size": 1.2, "entry_price": 790.25, "side": "sell"}, path=path)
        upsert_open_trade({"dealId": "DUP", "ticker": "Seagate Technology", "size": 1.2, "entry_price": 790.25, "side": "sell"}, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2, "test setup should have created two rows sharing the same dealId"
        assert all(t.get("status") == "OPEN" for t in trades)

        close_trade_by_dealId("DUP", exit_price=791.71, pnl=-1.29, path=path)

        trades = load_raw_log(path)
        assert all(t.get("status") == "CLOSED" for t in trades), "Every row sharing the dealId must be closed, not just the first"


class TestReconcileTickerMatching:
    """Regression tests: reconcile_with_positions() must match a bot/TradingView
    -opened pending trade by the broker's stable epic code, not the market's
    human-readable display symbol, or every such trade spawns a duplicate
    'Imported from live positions' log entry for the same real position."""

    def test_enriched_position_matches_pending_trade_by_epic_not_display_symbol(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        # order.py logs bot-opened trades with ticker=epic (e.g. "STX"), still
        # pending a real dealId until the broker's confirms endpoint responds.
        upsert_open_trade(
            {"dealId": None, "dealReference": "ref123", "ticker": "STX", "side": "sell",
             "size": 1.2, "entry_price": 790.25},
            path=path,
        )

        # session.enrich_positions() reports the live position with BOTH a
        # display "ticker" (market.symbol, e.g. the company name) and the
        # stable "epic" code alongside the broker-confirmed dealId.
        live_positions = [{
            "dealId": "D-REAL", "dealReference": None,
            "ticker": "Seagate Technology", "epic": "STX",
            "side": "sell", "size": 1.2, "entry_price": 790.25,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Must backfill the pending trade instead of creating a duplicate entry"
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL"

    def test_nested_broker_shape_matches_pending_trade_by_epic(self, tmp_path):
        """Same regression, but for the raw (unenriched) {'position', 'market'}
        broker payload shape."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": None, "dealReference": "ref123", "ticker": "STX", "side": "sell",
             "size": 1.2, "entry_price": 790.25},
            path=path,
        )

        live_positions = [{
            "position": {"dealId": "D-REAL", "direction": "SELL", "size": 1.2, "level": 790.25},
            "market": {"epic": "STX", "symbol": "Seagate Technology"},
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Must backfill the pending trade instead of creating a duplicate entry"
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL"


class TestFxRateValidation:
    """Tests for _read_fx_rate range validation."""

    def test_valid_fx_rate_used(self, monkeypatch):
        monkeypatch.setenv("FX_USD_GBP", "0.80")
        # Reload the function's env read
        from trade_log import _read_fx_rate
        # Clear any cached import
        import importlib, trade_log
        importlib.reload(trade_log)
        from trade_log import _read_fx_rate as fn
        assert fn() == pytest.approx(0.80)

    def test_out_of_range_fx_falls_back(self, monkeypatch):
        monkeypatch.setenv("FX_USD_GBP", "99.0")
        import importlib, trade_log
        importlib.reload(trade_log)
        from trade_log import _read_fx_rate as fn
        rate = fn()
        assert rate == pytest.approx(0.738), f"Out-of-range FX should fall back to default, got {rate}"

    def test_negative_fx_falls_back(self, monkeypatch):
        monkeypatch.setenv("FX_USD_GBP", "-0.5")
        import importlib, trade_log
        importlib.reload(trade_log)
        from trade_log import _read_fx_rate as fn
        rate = fn()
        assert rate == pytest.approx(0.738)


class TestSizing:
    def test_uses_equity_percent_and_leverage_without_cap(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 500}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 500.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 0)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 0)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA")

        assert result["blocked"] is False
        assert result["equity_used"] == pytest.approx(500.0)
        assert result["exposure"] == pytest.approx(2500.0)
        assert result["size"] == pytest.approx(25.0)

    def test_uses_lower_available_margin_when_below_cap(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 80}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 80.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA")

        assert result["blocked"] is False
        assert result["equity_used"] == pytest.approx(80.0)
        assert result["exposure"] == pytest.approx(400.0)
        assert result["size"] == pytest.approx(4.0)

    def test_equity_and_exposure_capped_at_configured_max(self, monkeypatch):
        """Even with a large available balance, equity/exposure per trade are capped."""
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA")

        assert result["blocked"] is False
        assert result["equity_used"] == pytest.approx(200.0)
        assert result["exposure"] == pytest.approx(1000.0)
        assert result["size"] == pytest.approx(10.0)


class TestThreadSafety:
    """Concurrent access tests for trade_log operations."""

    def test_concurrent_upserts_no_data_loss(self, tmp_path):
        """Multiple threads inserting distinct trades must all persist."""
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        errors = []

        def _insert(i):
            try:
                result = upsert_open_trade(
                    {"dealId": f"DID_{i}", "ticker": "T", "size": 1, "entry_price": 100 + i},
                    path=path,
                )
                if result is None:
                    errors.append(f"insert {i} returned None")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=_insert, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent inserts had errors: {errors}"
        trades = load_raw_log(path)
        assert len(trades) == 20, f"Expected 20 trades, got {len(trades)}"

    def test_concurrent_close_and_upsert(self, tmp_path):
        """Upsert and close of different trades concurrently must not corrupt log."""
        from trade_log import upsert_open_trade, close_trade_by_dealId, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        # Seed 10 open trades
        for i in range(10):
            upsert_open_trade(
                {"dealId": f"D_{i}", "ticker": "T", "size": 1, "entry_price": 100 + i},
                path=path,
            )

        errors = []

        def _close(i):
            try:
                close_trade_by_dealId(f"D_{i}", exit_price=110 + i, path=path)
            except Exception as e:
                errors.append(str(e))

        def _insert(i):
            try:
                upsert_open_trade(
                    {"dealId": f"NEW_{i}", "ticker": "T2", "size": 2, "entry_price": 200 + i},
                    path=path,
                )
            except Exception as e:
                errors.append(str(e))

        threads = (
            [threading.Thread(target=_close, args=(i,)) for i in range(10)] +
            [threading.Thread(target=_insert, args=(i,)) for i in range(10)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent operations had errors: {errors}"
        trades = load_raw_log(path)
        closed = [t for t in trades if t.get("status") == "CLOSED"]
        assert len(closed) == 10, f"Expected 10 closed trades, got {len(closed)}"


# ======================================================================== #
#  webhook: payload validation                                              #
# ======================================================================== #

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


# ======================================================================== #
#  dashboard: safe analytics defaults                                       #
# ======================================================================== #

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


class TestProtectedRoutes:
    def test_debug_route_requires_dashboard_auth(self):
        import webhook
        client = webhook.app.test_client()

        response = client.get("/debug/tokens")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard/login")

    def test_raw_route_requires_dashboard_auth(self):
        import webhook
        client = webhook.app.test_client()

        response = client.get("/raw")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard/login")


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


# ======================================================================== #
#  webhook: per-ticker order lock (prevents duplicate real broker orders)  #
# ======================================================================== #

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
