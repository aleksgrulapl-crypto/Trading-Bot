"""Tests for sizing and equity caps."""

import json
import os
import tempfile
import threading
import time
import sys
import pytest

import config


@pytest.fixture(autouse=True)
def _no_price_bands(monkeypatch):
    """Price-band unit caps are tested explicitly in TestPriceBands."""
    monkeypatch.setattr(config, "PRICE_BAND_MAX_UNITS", ())


def _make_tmp_log(initial_trades=None):
    """Create a temp JSON file holding *initial_trades* (default: [])."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(initial_trades or [], f)
    return path


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


class TestFixedSLTPEquityCap:
    """TradingView alert SL/TP is now used directly (sl_tp.FixedSLTP's own
    fixed-percentage levels are only a fallback, see webhook.py); the SL is
    capped so its implied loss never exceeds MAX_SL_PERC_OF_EQUITY of the
    equity used, while TP is never touched."""

    def test_buy_sl_within_cap_is_unchanged(self, monkeypatch):
        import sl_tp

        monkeypatch.setattr(sl_tp.config, "MAX_SL_PERC_OF_EQUITY", 0.20)
        monkeypatch.setattr(sl_tp.config, "LEVERAGE", 5)
        # Implied price move: (100-98)/100 = 2% < cap of 20%/5 = 4% -> unchanged.
        result = sl_tp.FixedSLTP.cap_sl_to_equity_risk(100.0, 98.0, "buy")
        assert result == pytest.approx(98.0)

    def test_buy_sl_beyond_cap_is_pulled_in(self, monkeypatch):
        import sl_tp

        monkeypatch.setattr(sl_tp.config, "MAX_SL_PERC_OF_EQUITY", 0.20)
        monkeypatch.setattr(sl_tp.config, "LEVERAGE", 5)
        # Alert SL implies a 50% price move, way past the 4% cap -> pulled to 96.0.
        result = sl_tp.FixedSLTP.cap_sl_to_equity_risk(100.0, 50.0, "buy")
        assert result == pytest.approx(96.0)

    def test_sell_sl_beyond_cap_is_pulled_in(self, monkeypatch):
        import sl_tp

        monkeypatch.setattr(sl_tp.config, "MAX_SL_PERC_OF_EQUITY", 0.20)
        monkeypatch.setattr(sl_tp.config, "LEVERAGE", 5)
        result = sl_tp.FixedSLTP.cap_sl_to_equity_risk(100.0, 150.0, "sell")
        assert result == pytest.approx(104.0)

    def test_sell_sl_within_cap_is_unchanged(self, monkeypatch):
        import sl_tp

        monkeypatch.setattr(sl_tp.config, "MAX_SL_PERC_OF_EQUITY", 0.20)
        monkeypatch.setattr(sl_tp.config, "LEVERAGE", 5)
        result = sl_tp.FixedSLTP.cap_sl_to_equity_risk(100.0, 102.0, "sell")
        assert result == pytest.approx(102.0)


class TestSizing:
    def test_uses_equity_percent_and_leverage_without_cap(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 500}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 500.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 0)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 0)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 0)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])

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
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 0)
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])

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
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA")

        assert result["blocked"] is False
        assert result["equity_used"] == pytest.approx(200.0)
        assert result["exposure"] == pytest.approx(1000.0)
        assert result["size"] == pytest.approx(10.0)

    def test_ticker_equity_cap_uses_remaining_capacity(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 100}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 100.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "OPEN", "size": 5.0, "entry_price": 100.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")

        assert result["blocked"] is False
        assert result["open_positions"] == 1
        assert result["equity_used_by_ticker"] == pytest.approx(100.0)
        assert result["equity_used"] == pytest.approx(100.0)
        assert result["size"] == pytest.approx(5.0)

    def test_hedge_sizing_ignores_opposite_side_ticker_usage(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "side": "buy", "status": "OPEN", "size": 10.0, "entry_price": 100.0},
        ])

        result = sizing.calculate_size(
            100,
            105,
            90,
            "sell",
            symbol="NVDA",
            ticker="NVDA",
            ignore_opposite_side_for_ticker_limits=True,
        )

        assert result["blocked"] is False
        assert result["open_positions"] == 0
        assert result["equity_used_by_ticker"] == pytest.approx(0.0)
        assert result["equity_used"] == pytest.approx(200.0)
        assert result["size"] == pytest.approx(10.0)

    def test_ticker_position_limit_blocks_fourth_trade(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "OPEN", "size": 2.0, "entry_price": 100.0},
            {"ticker": "NVDA", "status": "OPEN", "size": 2.0, "entry_price": 100.0},
            {"ticker": "NVDA", "status": "OPEN", "size": 2.0, "entry_price": 100.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")

        assert result["blocked"] is True
        assert result["reason"] == "max_positions_per_ticker_reached"

    def test_timeframe_cap_blocks_second_trade_on_same_timeframe(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME", 1)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 125)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 375)
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "OPEN", "size": 1.0, "entry_price": 100.0, "timeframe": "5M"},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA", timeframe="5M")

        assert result["blocked"] is True
        assert result["reason"] == "max_positions_per_ticker_timeframe_reached"

    def test_timeframe_cap_allows_other_timeframe_on_same_ticker(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME", 1)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 125)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 375)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "OPEN", "size": 1.0, "entry_price": 100.0, "timeframe": "5M"},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA", timeframe="15M")

        assert result["blocked"] is False
        assert result["equity_used_by_ticker"] == pytest.approx(20.0)

    def test_timeframe_cap_ignores_manual_na_trades(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER_PER_TIMEFRAME", 1)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 125)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 375)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "OPEN", "size": 1.0, "entry_price": 100.0, "timeframe": "N/A"},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA", timeframe="N/A")

        assert result["blocked"] is False

    def test_hedge_size_override_mirrors_hedged_trade_size(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        # Capacity caps intentionally tiny/exhausted to prove the override bypasses them.
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 0)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 1)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 1)
        # Account/ticker-usage lookups must not even be consulted for the override path.
        monkeypatch.setattr(sizing.session, "get_account", lambda: (_ for _ in ()).throw(AssertionError("should not fetch account")))
        monkeypatch.setattr(sizing, "load_raw_log", lambda: (_ for _ in ()).throw(AssertionError("should not load trade log")))

        result = sizing.calculate_size(
            100, 95, 110, "buy", symbol="NVDA", ticker="NVDA",
            hedge_size_override=3.0,
        )

        assert result["blocked"] is False
        assert result["size"] == pytest.approx(3.0)
        assert result["exposure"] == pytest.approx(300.0)
        assert result["equity_used"] == pytest.approx(60.0)
        assert result.get("hedge_mirrored_size") is True

    def test_ticker_usage_ignores_non_open_rows(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_POSITIONS_PER_TICKER", 3)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "CLOSED", "size": 5.0, "entry_price": 100.0},
            {"ticker": "NVDA", "status": "REJECTED", "size": 5.0, "entry_price": 100.0},
            {"ticker": "NVDA", "status": "OPEN", "size": 5.0, "entry_price": 100.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")

        assert result["blocked"] is False
        assert result["open_positions"] == 1
        assert result["equity_used_by_ticker"] == pytest.approx(100.0)

    def test_ticker_argument_is_used_for_min_size_lookup_without_symbol(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 100}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 100.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"SMALL": {"min_size": 2.5}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])

        result = sizing.calculate_size(300, 270, 330, "buy", ticker="SMALL")

        assert result["blocked"] is False
        assert result["min_size"] == pytest.approx(2.5)
        assert result["size"] == pytest.approx(2.5)

    def test_remaining_ticker_capacity_clamps_below_min_size(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"SMALL": {"min_size": 1.5}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "SMALL", "status": "OPEN", "size": 1.67, "entry_price": 299.4},
        ])

        result = sizing.calculate_size(300, 270, 330, "buy", ticker="SMALL")

        assert result["blocked"] is False
        assert result["size"] == pytest.approx(1.66)

    def test_remaining_ticker_capacity_below_min_size_blocks(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"SMALL": {"min_size": 2.5}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "SMALL", "status": "OPEN", "size": 1.67, "entry_price": 299.4},
        ])

        result = sizing.calculate_size(300, 270, 330, "buy", ticker="SMALL")

        assert result["blocked"] is True
        assert result["reason"] == "insufficient_ticker_capacity_for_min_size"

    def test_remaining_ticker_capacity_preserves_fitting_min_size(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 200)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"SMALL": {"min_size": 1.66}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "SMALL", "status": "OPEN", "size": 1.67, "entry_price": 299.4},
        ])

        result = sizing.calculate_size(300, 270, 330, "buy", ticker="SMALL")

        assert result["blocked"] is False
        assert result["size"] == pytest.approx(1.66)

    def test_ticker_with_no_closed_tradingview_trades_is_unscaled(self, monkeypatch):
        """A ticker not yet present in the Analytics "TradingView" tab (no
        decided closed trades) is skipped from equity scaling entirely."""
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 600)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(1.0)
        assert result["equity_used"] == pytest.approx(200.0)

    def test_winning_ticker_keeps_full_equity_cap(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 600)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"NVDA": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "NVDA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": 10.0},
            {"ticker": "NVDA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": 15.0},
            {"ticker": "NVDA", "status": "CLOSED", "trade_source": "hedge", "pnl_gbp": 5.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="NVDA", ticker="NVDA")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(1.0)
        assert result["equity_used"] == pytest.approx(200.0)

    def test_losing_ticker_equity_is_scaled_down_but_not_to_zero(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 600)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_EQUITY_SCALE_MIN", 0.3)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"AMD": {"min_size": 0.1}})
        # 5 losing trades -> net negative with >= 5 trades -> halved.
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "AMD", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -10.0}
            for _ in range(5)
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="AMD", ticker="AMD")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(0.5)
        assert result["equity_used"] == pytest.approx(100.0)

    def test_mixed_win_rate_scales_equity_proportionally(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 600)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_EQUITY_SCALE_MIN", 0.3)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"TSLA": {"min_size": 0.1}})
        # 5 trades, net positive with positive Sharpe, outside the bottom 20% -> upsized by 20%.
        others = [
            {"ticker": t, "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": p}
            for t in ("A1", "A2", "A3", "A4")
            for p in (30.0, -10.0, 25.0, -8.0, 12.0)
        ]
        monkeypatch.setattr(sizing, "load_raw_log", lambda: others + [
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": p}
            for p in (10.0, 12.0, 10.0, -5.0, 8.0)
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="TSLA", ticker="TSLA")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(1.2)
        assert result["equity_used"] == pytest.approx(240.0)

    def test_fewer_than_five_trades_not_scaled(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -10.0}
            for _ in range(4)
        ])
        assert sizing._ticker_equity_scale("TSLA") == pytest.approx(1.0)

    def test_open_and_non_tradingview_trades_are_excluded_from_win_rate(self, monkeypatch):
        """Only CLOSED TradingView/Hedge trades count; OPEN trades and
        "Trader" (manual/discretionary) trades don't affect the scale."""
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 200)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 600)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 1000)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"MSFT": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "MSFT", "status": "OPEN", "trade_source": "tradingview", "pnl_gbp": -500.0},
            {"ticker": "MSFT", "status": "CLOSED", "trade_source": "manual", "pnl_gbp": -500.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="MSFT", ticker="MSFT")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(1.0)
        assert result["equity_used"] == pytest.approx(200.0)


class TestPriceBands:
    BANDS = ((10.0, 75.0), (50.0, 15.0), (100.0, 7.5), (250.0, 3.0), (500.0, 1.5))

    def _setup(self, monkeypatch):
        import sizing

        monkeypatch.setattr(sizing.session, "get_account", lambda: {"balance": {"available": 5000}})
        monkeypatch.setattr(sizing.session, "enrich_account", lambda raw: {"available": 5000.0})
        monkeypatch.setattr(sizing.config, "EQUITY_PERCENT", 1.0)
        monkeypatch.setattr(sizing.config, "LEVERAGE", 5)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TRADE", 150)
        monkeypatch.setattr(sizing.config, "MAX_EQUITY_PER_TICKER", 450)
        monkeypatch.setattr(sizing.config, "MAX_EXPOSURE_PER_TRADE", 750)
        monkeypatch.setattr(sizing.config, "FX_USD_GBP", 1.0)
        monkeypatch.setattr(sizing.config, "PRICE_BAND_MAX_UNITS", self.BANDS)
        monkeypatch.setattr(sizing.config, "TICKER_SETTINGS", {"X": {"min_size": 0.1}})
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [])
        return sizing

    @pytest.mark.parametrize("price,expected", [
        (5, 75.0), (9.99, 75.0), (20, 15.0), (75, 7.5), (150, 3.0), (400, 1.5),
    ])
    def test_band_caps_units(self, monkeypatch, price, expected):
        sizing = self._setup(monkeypatch)
        result = sizing.calculate_size(price, price * 0.95, price * 1.1, "buy", symbol="X", ticker="X")
        assert result["size"] == pytest.approx(expected)

    def test_above_top_band_uses_equity_sizing(self, monkeypatch):
        sizing = self._setup(monkeypatch)
        result = sizing.calculate_size(1000, 950, 1100, "buy", symbol="X", ticker="X")
        assert result["size"] == pytest.approx(0.75)
        assert result["equity_used"] == pytest.approx(150.0)

    def test_band_cap_scales_with_performance(self, monkeypatch):
        sizing = self._setup(monkeypatch)
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "X", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -10.0}
            for _ in range(5)
        ])
        result = sizing.calculate_size(20, 19, 22, "buy", symbol="X", ticker="X")
        assert result["size"] == pytest.approx(7.5)
