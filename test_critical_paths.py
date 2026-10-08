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

    def test_broker_confirmed_dealid_rebinds_tradingview_row_with_stale_local_dealid(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-UNH-1",
                "dealReference": None,
                "ticker": "UNH",
                "side": "sell",
                "size": 0.1,
                "entry_price": 386.2,
                "time_entered": "2026-09-11T15:00:23Z",
                "trade_source": "tradingview",
            },
            path=path,
        )
        upsert_open_trade(
            {
                "dealId": "D-REAL-UNH-1",
                "ticker": "UNH",
                "side": "sell",
                "size": 0.1,
                "entry_price": 386.2,
                "time_entered": "2026-09-11T15:00:24Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["dealId"] == "D-REAL-UNH-1"
        assert trades[0]["trade_source"] == "tradingview"

    def test_broker_confirmed_dealid_rebinds_tradingview_row_with_same_dealreference(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-PLTR-1",
                "dealReference": "REF-PLTR-1",
                "ticker": "PLTR",
                "side": "sell",
                "size": 0.67,
                "entry_price": 166.7,
                "time_entered": "2026-09-11T15:30:11Z",
                "trade_source": "tradingview",
            },
            path=path,
        )
        upsert_open_trade(
            {
                "dealId": "D-REAL-PLTR-1",
                "dealReference": "REF-PLTR-1",
                "ticker": "PLTR",
                "side": "sell",
                "size": 0.6,
                "entry_price": 166.7,
                "time_entered": "2026-09-11T15:30:24Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["dealId"] == "D-REAL-PLTR-1"
        assert trades[0]["dealReference"] == "REF-PLTR-1"
        assert trades[0]["size"] == pytest.approx(0.6)
        assert trades[0]["trade_source"] == "tradingview"

    def test_broker_confirmed_dealid_rebinds_tradingview_row_when_confirm_omits_dealreference(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-STX-1",
                "dealReference": "REF-STX-1",
                "ticker": "STX",
                "side": "sell",
                "size": 0.11,
                "entry_price": 818.17,
                "time_entered": "2026-09-11T17:30:12Z",
                "trade_source": "tradingview",
            },
            path=path,
        )
        upsert_open_trade(
            {
                "dealId": "D-REAL-STX-1",
                "dealReference": None,
                "ticker": "STX",
                "side": "sell",
                "size": 0.10,
                "entry_price": 818.17,
                "time_entered": "2026-09-11T17:33:37Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["dealId"] == "D-REAL-STX-1"
        assert trades[0]["dealReference"] == "REF-STX-1"
        assert trades[0]["size"] == pytest.approx(0.10)
        assert trades[0]["trade_source"] == "tradingview"

    def test_broker_confirmed_payload_refreshes_existing_tradingview_fill_details(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "D-REAL-MSFT-1",
                "dealReference": "REF-MSFT-1",
                "ticker": "MSFT",
                "side": "sell",
                "size": 0.4,
                "entry_price": 494.0,
                "time_entered": "2026-09-11T15:45:12Z",
                "trade_source": "tradingview",
            },
            path=path,
        )
        upsert_open_trade(
            {
                "dealId": "D-REAL-MSFT-1",
                "dealReference": "REF-MSFT-1",
                "ticker": "MSFT",
                "side": "sell",
                "size": 0.4,
                "entry_price": 494.05,
                "time_entered": "2026-09-11T15:45:14Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(494.05)
        assert trades[0]["trade_source"] == "tradingview"

    def test_second_pending_scale_in_does_not_merge_into_first_pending_row(self, tmp_path):
        """Two distinct TradingView signals for the same ticker/side (e.g. a
        persistent Supertrend condition re-firing, or a deliberate scale-in)
        each log their own pending (dealId-less) row keyed by dealReference.

        Before the fix, order.py's second pending_payload append (dealId=None,
        dealReference=refB) would incorrectly merge into the first still-open
        row (dealReference=refA) via _find_open_trade_by_ticker_any_dealid's
        "not dealId and dealReference" fallback, because that fallback ignored
        dealReference entirely. This silently discarded refB and corrupted the
        first row's entry_price/size, leaving the second order's real dealId
        to later orphan into a brand-new 'Trader'-labeled duplicate once
        reconcile_with_positions() saw it.
        """
        from trade_log import upsert_open_trade, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": None, "dealReference": "refA", "ticker": "ORCL", "side": "long",
                "size": 0.6, "entry_price": 146.73, "trade_source": "tradingview",
                "time_entered": "2026-10-10T14:45:05Z",
            },
            path=path,
        )
        upsert_open_trade(
            {
                "dealId": None, "dealReference": "refB", "ticker": "ORCL", "side": "long",
                "size": 0.6, "entry_price": 146.80, "trade_source": "tradingview",
                "time_entered": "2026-10-10T14:45:55Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 2
        refs = {t.get("dealReference") for t in trades}
        assert refs == {"refA", "refB"}
        for t in trades:
            assert t["trade_source"] == "tradingview"
            assert t["status"] == "OPEN"

    def test_two_scale_in_signals_full_lifecycle_never_creates_trader_orphan(self, tmp_path):
        """End-to-end reproduction of the ORCL/PLTR duplicate: two TradingView
        signals for the same ticker/side, each going through order.py's real
        pending -> confirmed-dealId lifecycle, followed by a reconcile pass
        that sees both broker positions. Neither signal should ever surface as
        an 'Imported from live positions' row defaulted to trade_source
        'trader'."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        # Signal 1 pending, then confirmed with its real dealId.
        upsert_open_trade(
            {"dealId": None, "dealReference": "refA", "ticker": "ORCL", "side": "long",
             "size": 0.6, "entry_price": 146.73, "trade_source": "tradingview",
             "time_entered": "2026-10-10T14:45:05Z"},
            path=path,
        )
        # Signal 2 fires before signal 1's confirms poll completes.
        upsert_open_trade(
            {"dealId": None, "dealReference": "refB", "ticker": "ORCL", "side": "long",
             "size": 0.6, "entry_price": 146.80, "trade_source": "tradingview",
             "time_entered": "2026-10-10T14:45:55Z"},
            path=path,
        )
        # Signal 1's confirms poll resolves.
        upsert_open_trade(
            {"dealId": "DEAL-ORCL-1", "dealReference": "refA", "ticker": "ORCL", "side": "long",
             "size": 0.6, "entry_price": 146.73, "trade_source": "tradingview",
             "time_entered": "2026-10-10T14:45:05Z"},
            path=path,
        )
        # Signal 2's confirms poll resolves.
        upsert_open_trade(
            {"dealId": "DEAL-ORCL-2", "dealReference": "refB", "ticker": "ORCL", "side": "long",
             "size": 0.6, "entry_price": 146.80, "trade_source": "tradingview",
             "time_entered": "2026-10-10T14:45:55Z"},
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 2
        assert {t["dealId"] for t in trades} == {"DEAL-ORCL-1", "DEAL-ORCL-2"}
        assert all(t["trade_source"] == "tradingview" for t in trades)

        # A later dashboard load reconciles both live broker positions.
        result = reconcile_with_positions([
            {"dealId": "DEAL-ORCL-1", "epic": "ORCL", "side": "buy", "size": 0.6,
             "entry_price": 146.73, "time_entered": "2026-10-10T14:45:05Z"},
            {"dealId": "DEAL-ORCL-2", "epic": "ORCL", "side": "buy", "size": 0.6,
             "entry_price": 146.80, "time_entered": "2026-10-10T14:45:55Z"},
        ], path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2, "reconcile must not create a phantom third row"
        assert not result["added"]
        assert all(t["trade_source"] == "tradingview" for t in trades)
        assert all(t["status"] == "OPEN" for t in trades)


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

    def test_live_position_replaces_dealreference_placeholder_without_duplicate(self, tmp_path):
        from trade_log import load_raw_log, reconcile_with_positions, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([{
            "dealId": "REF-PLACEHOLDER",
            "dealReference": "REF-PLACEHOLDER",
            "ticker": "NVDA",
            "side": "long",
            "size": 0.59,
            "entry_price": 219.08,
            "time_entered": "2026-09-22T10:00:00+01:00",
            "status": "OPEN",
            "trade_source": "tradingview",
            "origin": "tradingview",
            "notes": "sl=210; tp=230; timeframe=1h; dealReference=REF-PLACEHOLDER",
        }], path=path)

        result = reconcile_with_positions([{
            "dealId": "DEAL-NVDA-REAL",
            "ticker": "NVDA",
            "side": "buy",
            "size": 0.5,
            "entry_price": 219.42,
            "time_entered": "2026-09-22T10:00:04+01:00",
        }], path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "DEAL-NVDA-REAL"
        assert trades[0]["dealReference"] == "REF-PLACEHOLDER"
        assert trades[0]["trade_source"] == "tradingview"

    def test_live_position_with_distinct_dealid_creates_second_open_trade(self, tmp_path):
        """Distinct broker dealIds for the same ticker/side must remain distinct
        open trades so scale-ins do not overwrite the first row."""
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

        # Broker reports another open position for the same ticker/side.
        live_positions = [{
            "dealId": "D-NEW", "dealReference": None, "ticker": "MRVL", "side": "sell",
            "size": 4.88, "entry_price": 204.95,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2
        assert len(result["added"]) == 1
        assert {t["dealId"] for t in trades} == {"D-OLD", "D-NEW"}

    def test_reconcile_mislabelled_trader_row_is_corrected_once_pending_append_lands(self, tmp_path):
        """Regression test for the NBIS-style phantom 'Trader' duplicate.

        If a dashboard auto-refresh's reconcile_with_positions() call races
        ahead of order.py's own pending-row append (the broker fills and
        reports the live position before this bot's own request thread has
        written anything to the trade log), reconcile has nothing to match
        against and must create a brand-new row - which _detect_trade_origin
        always labels "trader" for a raw broker position. Once order.py's own
        pending append (carrying a dealReference and a trustworthy
        tradingview origin) catches up a moment later, it must merge into
        that same row *and* correct the "trader" mislabel to "tradingview"
        instead of leaving it stuck permanently wrong.
        """
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        # Dashboard refresh lands first: broker already reports the live
        # position, but order.py hasn't appended its own pending row yet.
        live_positions = [{
            "dealId": "DEAL-NBIS", "dealReference": None, "ticker": "NBIS", "side": "sell",
            "size": 2.65, "entry_price": 235.51,
        }]
        reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["trade_source"] == "trader"

        # order.py's own pending append now lands moments later, carrying
        # trustworthy TradingView provenance (dealReference + origin).
        upsert_open_trade(
            {"dealReference": "REF-NBIS", "ticker": "NBIS", "side": "sell",
             "size": 2.65, "entry_price": 235.51, "origin": "tradingview"},
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["dealId"] == "DEAL-NBIS"
        assert trades[0]["dealReference"] == "REF-NBIS"

    def test_reconcile_na_timeframe_placeholder_is_corrected_by_later_upsert(self, tmp_path):
        """Regression test: reconcile's 'N/A' timeframe placeholder must not
        block a later payload's real parsed timeframe from ever being applied.

        reconcile_with_positions() always stamps timeframe="N/A" on a row it
        creates for a live broker position (it has no concept of timeframe).
        upsert_open_trade()'s merge previously only filled in timeframe when
        the field was empty/None, so once "N/A" was set it was treated as an
        already-known value and the real timeframe from order.py's own
        pending/confirmed append could never overwrite it.
        """
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        live_positions = [{
            "dealId": "DEAL-BE", "dealReference": None, "ticker": "BE", "side": "buy",
            "size": 2.14, "entry_price": 291.96,
        }]
        reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["timeframe"] == "N/A"

        upsert_open_trade(
            {"dealReference": "REF-BE", "ticker": "BE", "side": "buy",
             "size": 2.14, "entry_price": 291.96, "origin": "tradingview",
             "timeframe": "15M"},
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["timeframe"] == "15M"

    def test_dangling_tradingview_row_collapses_into_dealid_trader_duplicate(self, tmp_path):
        """Regression test for phantom 'Trader' duplicates of a TradingView trade.

        Reproduces the dashboard screenshot bug: a webhook-sourced pending row
        (no dealId, origin=tradingview) is left open forever while a second,
        dealId'd row for the same ticker/side/entry price gets created shortly
        after (e.g. via reconcile's live-position import) and mislabelled
        "Trader" because its origin could not be determined. Both rows must
        collapse into a single row with TradingView provenance preserved.
        """
        from trade_log import reconcile_with_positions, load_raw_log, save_raw_log

        path = str(tmp_path / "log.json")
        save_raw_log([
            {
                "dealId": None,
                "dealReference": "refMSFT1",
                "ticker": "MSFT",
                "side": "long",
                "size": 0.32,
                "entry_price": 518.02,
                "time_entered": "2026-10-05T11:31:02+01:00",
                "status": "OPEN",
                "trade_source": "tradingview",
                "origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
            {
                "dealId": "D-MSFT-REAL",
                "dealReference": None,
                "ticker": "MSFT",
                "side": "long",
                "size": 0.32,
                "entry_price": 518.02,
                "time_entered": "2026-10-05T11:39:15+01:00",
                "status": "CLOSED",
                "exit_price": 519.39,
                "time_exited": "2026-10-05T13:20:44+01:00",
                "pnl": 0.32,
                "trade_source": "trader",
                "origin": "trader",
                "notes": "Imported from live positions",
            },
        ], path=path)

        # No live positions (the trade already closed) – reconcile should still
        # self-heal the duplicate via its canonicalize_trade_log() dedupe pass.
        reconcile_with_positions([], path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Dangling TradingView row and its dealId'd duplicate must merge into one"
        merged = trades[0]
        assert merged["dealId"] == "D-MSFT-REAL"
        assert merged["status"] == "CLOSED"
        assert merged["trade_source"] == "tradingview"
        assert merged["origin"] == "tradingview"

    def test_live_position_rebinds_tradingview_row_with_stale_local_dealid(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-SPXC-1",
                "dealReference": None,
                "ticker": "SPXC",
                "side": "sell",
                "size": 0.35,
                "entry_price": 148.01,
                "time_entered": "2026-09-11T15:00:07Z",
                "trade_source": "tradingview",
            },
            path=path,
        )

        result = reconcile_with_positions(
            [{
                "dealId": "D-REAL-SPXC-1",
                "dealReference": None,
                "ticker": "SPXC",
                "side": "sell",
                "size": 0.3,
                "entry_price": 148.02,
                "time_entered": "2026-09-11T15:00:24Z",
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-SPXC-1"
        assert trades[0]["trade_source"] == "tradingview"

    def test_live_position_rebinds_tradingview_row_with_same_dealreference(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-MSFT-1",
                "dealReference": "REF-MSFT-1",
                "ticker": "MSFT",
                "side": "sell",
                "size": 0.4,
                "entry_price": 494.05,
                "time_entered": "2026-09-11T15:45:12Z",
                "trade_source": "tradingview",
            },
            path=path,
        )

        result = reconcile_with_positions(
            [{
                "position": {
                    "dealId": "D-REAL-MSFT-1",
                    "dealReference": "REF-MSFT-1",
                    "direction": "SELL",
                    "size": 0.4,
                    "level": 494.05,
                    "createdDate": "2026-09-11T15:45:14Z",
                },
                "market": {"epic": "MSFT", "symbol": "Microsoft"},
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-MSFT-1"
        assert trades[0]["dealReference"] == "REF-MSFT-1"
        assert trades[0]["trade_source"] == "tradingview"

    def test_live_position_rebinds_tradingview_row_when_positions_omit_dealreference(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-STX-2",
                "dealReference": "REF-STX-2",
                "ticker": "STX",
                "side": "sell",
                "size": 0.11,
                "entry_price": 818.17,
                "time_entered": "2026-09-11T17:30:12Z",
                "trade_source": "tradingview",
            },
            path=path,
        )

        result = reconcile_with_positions(
            [{
                "position": {
                    "dealId": "D-REAL-STX-2",
                    "dealReference": None,
                    "direction": "SELL",
                    "size": 0.10,
                    "level": 818.17,
                    "createdDate": "2026-09-11T17:33:37Z",
                },
                "market": {"epic": "STX", "symbol": "Seagate Technology"},
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-STX-2"
        assert trades[0]["dealReference"] == "REF-STX-2"
        assert trades[0]["size"] == pytest.approx(0.10)
        assert trades[0]["trade_source"] == "tradingview"

    def test_reconcile_removes_lingering_tradingview_duplicate_when_broker_row_already_exists(self, tmp_path):
        from trade_log import reconcile_with_positions, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([
            {
                "dealId": "D-REAL-UNH-2",
                "dealReference": None,
                "ticker": "UNH",
                "side": "short",
                "size": 0.5,
                "entry_price": 378.75,
                "time_entered": "2026-09-14T12:21:15Z",
                "status": "OPEN",
                "trade_source": "trader",
                "origin": "trader",
                "notes": "Imported from live positions",
            },
            {
                "dealId": "TV-LOCAL-UNH-2",
                "dealReference": None,
                "ticker": "UNH",
                "side": "short",
                "size": 0.56,
                "entry_price": 378.75,
                "time_entered": "2026-09-14T12:15:45Z",
                "status": "OPEN",
                "trade_source": "tradingview",
                "origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
        ], path=path)

        result = reconcile_with_positions(
            [{
                "dealId": "D-REAL-UNH-2",
                "dealReference": None,
                "ticker": "UNH",
                "side": "sell",
                "size": 0.5,
                "entry_price": 378.75,
                "time_entered": "2026-09-14T12:21:15Z",
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-UNH-2"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["trusted_origin"] == "tradingview"
        assert trades[0]["time_entered"] == "2026-09-14T12:21:15Z"
        assert "Imported from live positions" in (trades[0].get("notes") or "")
        assert "Imported from webhook (tradingview)" in (trades[0].get("notes") or "")

    def test_reconcile_removes_same_dealid_tradingview_duplicate_with_fill_mismatch(self, tmp_path):
        from trade_log import reconcile_with_positions, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([
            {
                "dealId": "D-REAL-INTC-OPEN",
                "dealReference": None,
                "ticker": "INTC",
                "side": "long",
                "size": 10.0,
                "entry_price": 96.36,
                "time_entered": "2026-09-14T15:45:05Z",
                "status": "OPEN",
                "trade_source": "trader",
                "origin": "trader",
                "notes": "Imported from live positions",
            },
            {
                "dealId": "D-REAL-INTC-OPEN",
                "dealReference": None,
                "ticker": "INTC",
                "side": "long",
                "size": 10.37,
                "entry_price": 96.36,
                "time_entered": "2026-09-14T15:45:03Z",
                "status": "OPEN",
                "trade_source": "tradingview",
                "origin": "tradingview",
                "trusted_origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
        ], path=path)

        result = reconcile_with_positions(
            [{
                "dealId": "D-REAL-INTC-OPEN",
                "dealReference": None,
                "ticker": "INTC",
                "side": "buy",
                "size": 10.0,
                "entry_price": 96.36,
                "time_entered": "2026-09-14T15:45:05Z",
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-INTC-OPEN"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["trusted_origin"] == "tradingview"
        assert trades[0]["size"] == pytest.approx(10.0)

    def test_broker_upsert_removes_lingering_tradingview_duplicate_when_broker_row_already_exists(self, tmp_path):
        from trade_log import upsert_open_trade, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([
            {
                "dealId": "D-REAL-AMZN-2",
                "dealReference": None,
                "ticker": "AMZN",
                "side": "long",
                "size": 1.3,
                "entry_price": 255.65,
                "time_entered": "2026-09-14T12:21:15Z",
                "status": "OPEN",
                "trade_source": "trader",
                "origin": "trader",
                "notes": "Imported from live positions",
            },
            {
                "dealId": None,
                "dealReference": None,
                "ticker": "AMZN",
                "side": "long",
                "size": 1.33,
                "entry_price": 255.65,
                "time_entered": "2026-09-14T12:15:20Z",
                "status": "OPEN",
                "trade_source": "tradingview",
                "origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
        ], path=path)

        upsert_open_trade(
            {
                "dealId": "D-REAL-AMZN-2",
                "dealReference": None,
                "ticker": "AMZN",
                "side": "buy",
                "size": 1.3,
                "entry_price": 255.65,
                "time_entered": "2026-09-14T12:21:15Z",
            },
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["dealId"] == "D-REAL-AMZN-2"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["trusted_origin"] == "tradingview"
        assert trades[0]["time_entered"] == "2026-09-14T12:21:15Z"
        assert "Imported from live positions" in (trades[0].get("notes") or "")
        assert "Imported from webhook (tradingview)" in (trades[0].get("notes") or "")

    def test_live_position_rebinds_single_trusted_tradingview_row_even_with_large_size_mismatch(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-AMZN-3",
                "dealReference": "REF-AMZN-3",
                "ticker": "AMZN",
                "side": "sell",
                "size": 2.65,
                "entry_price": 251.8,
                "time_entered": "2026-09-14T15:30:15Z",
                "trade_source": "tradingview",
                "origin": "tradingview",
                "trusted_origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
            path=path,
        )

        result = reconcile_with_positions(
            [{
                "dealId": "D-REAL-AMZN-3",
                "dealReference": None,
                "ticker": "AMZN",
                "side": "sell",
                "size": 2.0,
                "entry_price": 251.8,
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-AMZN-3"
        assert trades[0]["dealReference"] == "REF-AMZN-3"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"

    def test_live_position_rebinds_trusted_tradingview_row_without_dealreference_when_size_rounded(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": "TV-LOCAL-INTC-1",
                "dealReference": None,
                "ticker": "INTC",
                "side": "sell",
                "size": 10.37,
                "entry_price": 96.36,
                "trade_source": "tradingview",
                "origin": "tradingview",
                "trusted_origin": "tradingview",
                "notes": "Imported from webhook (tradingview)",
            },
            path=path,
        )

        result = reconcile_with_positions(
            [{
                "dealId": "D-REAL-INTC-1",
                "dealReference": None,
                "ticker": "INTC",
                "side": "sell",
                "size": 10.0,
                "entry_price": 96.36,
                # Simulate the partial broker payload shape that can omit time_entered.
                "time_entered": None,
            }],
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert not result["added"]
        assert trades[0]["dealId"] == "D-REAL-INTC-1"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["size"] == pytest.approx(10.0)

    def test_real_dealid_replaces_dealreference_placeholder_before_false_close(self, tmp_path):
        """Regression for the ORCL phantom close: an early raw broker payload may
        have only dealReference, but once the real dealId arrives it must replace
        any temporary placeholder instead of leaving the row tracked under the
        reference string and later auto-closing it as 'missing'."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": None, "ticker": "ORCL", "side": "sell",
             "size": 3.1, "entry_price": 161.7, "time_entered": "2026-09-09T21:00:36Z"},
            path=path,
        )

        reconcile_with_positions([{
            "position": {
                "dealReference": "REF123",
                "direction": "SELL",
                "size": 3.13,
                "level": 161.7,
                "createdDate": "2026-09-09T21:00:36Z",
            },
            "market": {"epic": "ORCL", "symbol": "Oracle"},
        }], path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["dealId"] is None, "dealReference must not be promoted into dealId"
        assert trades[0]["dealReference"] == "REF123"

        upsert_open_trade(
            {"dealId": "REAL123", "dealReference": "REF123", "ticker": "ORCL", "side": "sell",
             "size": 3.1, "entry_price": 161.7, "time_entered": "2026-09-09T21:00:36Z"},
            path=path,
        )

        trades = load_raw_log(path)
        assert len(trades) == 1, "The real dealId must backfill the existing row, not create a duplicate"
        assert trades[0]["dealId"] == "REAL123"
        assert trades[0]["dealReference"] == "REF123"
        assert trades[0]["status"] == "OPEN"

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

    def test_live_position_epic_symbol_alias_backfills_pending_trade(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {
                "dealId": None,
                "dealReference": "REF-NVDA-1",
                "ticker": "NVDA",
                "side": "sell",
                "size": 0.59,
                "entry_price": 219.08,
                "trade_source": "tradingview",
            },
            path=path,
        )

        live_positions = [{
            "position": {
                "dealId": "DEAL-NVDA-1",
                "dealReference": "REF-NVDA-1",
                "direction": "SELL",
                "size": 0.5,
                "level": 219.08,
                "createdDate": "2026-09-11T13:17:17Z",
            },
            "market": {"epic": "US.NVDA.CASH", "symbol": "NVDA"},
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Alias ticker forms must merge into the same trade row"
        assert not result["added"]
        assert trades[0]["dealId"] == "DEAL-NVDA-1"
        assert trades[0]["size"] == pytest.approx(0.5)
        assert trades[0]["trade_source"] == "tradingview"

    def test_epic_alias_extraction_supports_index_and_fx_formats(self):
        from trade_log import _ticker_aliases

        assert "dax" in _ticker_aliases("IX.D.DAX.IFD.IP", include_epic_symbol_alias=True)
        assert "eurusd" in _ticker_aliases("CS.D.EURUSD.CFD.IP", include_epic_symbol_alias=True)

    def test_broker_synthetic_position_dealreference_does_not_block_rebind(self, tmp_path):
        """Regression test for the GOOG-style phantom 'Trader' duplicate.

        Capital.com's live positions snapshot can report an existing
        TradingView position under a brand-new dealId alongside a
        dealReference formatted as "p_<dealId>" - a broker-generated
        position-side placeholder, not the original "o_<uuid>"-style
        reference captured when the order was placed. A strict
        dealReference equality check then always fails (the placeholder
        never equals the stored reference), blocking the dealId rebind and
        causing reconcile_with_positions() to log a brand-new duplicate
        'Trader' row for a position that is already open and tracked.
        """
        from trade_log import reconcile_with_positions, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([{
            "dealId": "001cf3c7-0001-54c4-0000-000080ec000f",
            "dealReference": "o_123f8910-652c-48e3-99b4-237207add515",
            "ticker": "GOOG",
            "side": "long",
            "size": 1.81,
            "entry_price": 344.37,
            "time_entered": "2026-10-05T19:00:30.870461+01:00",
            "status": "OPEN",
            "trade_source": "tradingview",
            "origin": "tradingview",
        }], path=path)

        live_positions = [{
            "dealId": "001cf3c7-0001-54c4-0000-000080ec0010",
            "dealReference": "p_001cf3c7-0001-54c4-0000-000080ec0010",
            "ticker": "GOOG",
            "side": "long",
            "size": 1.8,
            "entry_price": 344.37,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Must rebind the existing TradingView row, not create a duplicate Trader row"
        assert not result["added"]
        assert trades[0]["dealId"] == "001cf3c7-0001-54c4-0000-000080ec0010"
        assert trades[0]["trade_source"] == "tradingview"


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
        from trade_log import close_trade_by_dealId, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        # Construct the duplicate-dealId scenario directly (upsert_open_trade
        # itself would correctly match the second call to the first row by
        # dealId, so this bypasses that to simulate a duplicate that already
        # slipped into the log another way, e.g. the reconcile ticker bug).
        duplicated_rows = [
            {"dealId": "DUP", "ticker": "STX", "side": "short", "size": 1.2,
             "entry_price": 790.25, "status": "OPEN"},
            {"dealId": "DUP", "ticker": "Seagate Technology", "side": "short", "size": 1.2,
             "entry_price": 790.25, "status": "OPEN"},
        ]
        save_raw_log(duplicated_rows, path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2, "test setup should have created two rows sharing the same dealId"
        assert all(t.get("status") == "OPEN" for t in trades)

        close_trade_by_dealId("DUP", exit_price=791.71, pnl=-1.29, path=path)

        trades = load_raw_log(path)
        assert all(t.get("status") == "CLOSED" for t in trades), "Every row sharing the dealId must be closed, not just the first"

    def test_webhook_source_is_normalized_to_tradingview(self, tmp_path):
        from trade_log import upsert_open_trade
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        trade = upsert_open_trade({
            "dealId": "TV1",
            "ticker": "STX",
            "size": 1.2,
            "entry_price": 790.25,
            "side": "buy",
            "trade_source": "webhook",
        }, path=path)

        assert trade["trade_source"] == "tradingview"

    def test_live_position_import_defaults_to_trader(self, tmp_path):
        from trade_log import reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        reconcile_with_positions([{
            "dealId": "MAN1",
            "ticker": "NVDA",
            "side": "buy",
            "size": 1.0,
            "entry_price": 200.0,
        }], path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["trade_source"] == "trader"

    def test_canonicalize_trade_log_merges_duplicate_closed_rows_only_when_same_event(self, tmp_path):
        from trade_log import canonicalize_trade_log, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        duplicated_rows = [
            {"dealId": "DUP", "ticker": "STX", "side": "short", "size": 1.2,
             "entry_price": 790.25, "exit_price": 791.71,
             "time_entered": "2026-09-08T10:00:00Z", "time_exited": "2026-09-08T12:00:00Z",
             "status": "CLOSED", "notes": "Closed via sync"},
            {"dealId": "DUP", "ticker": "Seagate Technology", "side": "short", "size": 1.2,
             "entry_price": 790.25, "exit_price": 791.71,
             "time_entered": "2026-09-08T10:01:00Z", "time_exited": "2026-09-08T12:00:00Z",
             "status": "CLOSED", "notes": "Imported from live positions"},
        ]
        save_raw_log(duplicated_rows, path=path)

        canonicalize_trade_log(path=path)

        trades = load_raw_log(path)
        assert len(trades) == 1, "Same-event duplicate closed rows should be collapsed into one canonical record"
        assert "Closed via sync" in (trades[0].get("notes") or "")
        assert "Imported from live positions" in (trades[0].get("notes") or "")

    def test_close_trade_by_dealid_collapses_same_event_duplicates_with_fill_mismatch(self, tmp_path):
        from trade_log import close_trade_by_dealId, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        save_raw_log([
            {"dealId": "DUP-INTC", "ticker": "INTC", "side": "long", "size": 10.0,
             "entry_price": 96.36, "time_entered": "2026-09-14T15:45:05Z",
             "status": "OPEN", "trade_source": "trader", "origin": "trader",
             "notes": "Imported from live positions"},
            {"dealId": "DUP-INTC", "ticker": "INTC", "side": "long", "size": 10.37,
             "entry_price": 96.36, "time_entered": "2026-09-14T15:45:03Z",
             "status": "OPEN", "trade_source": "tradingview", "origin": "tradingview",
             "trusted_origin": "tradingview", "notes": "Imported from webhook (tradingview)"},
        ], path=path)

        closed = close_trade_by_dealId("DUP-INTC", exit_price=97.11, time_exited="2026-09-14T16:00:00Z", path=path)

        trades = load_raw_log(path)
        assert closed is not None
        assert len(trades) == 1
        assert trades[0]["status"] == "CLOSED"
        assert trades[0]["trade_source"] == "tradingview"
        assert trades[0]["origin"] == "tradingview"
        assert trades[0]["trusted_origin"] == "tradingview"

    def test_canonicalize_trade_log_keeps_reused_dealid_trades_separate(self, tmp_path):
        from trade_log import canonicalize_trade_log, load_raw_log, save_raw_log
        path = str(tmp_path / "log.json")
        reused_dealid_rows = [
            {"dealId": "REUSED", "ticker": "STX", "side": "short", "size": 1.2,
             "entry_price": 790.25, "exit_price": 791.71,
             "time_entered": "2026-09-01T10:00:00Z", "time_exited": "2026-09-01T12:00:00Z",
             "status": "CLOSED"},
            {"dealId": "REUSED", "ticker": "STX", "side": "short", "size": 1.2,
             "entry_price": 790.25, "exit_price": 788.10,
             "time_entered": "2026-09-08T10:00:00Z", "time_exited": "2026-09-08T12:00:00Z",
             "status": "CLOSED"},
        ]
        save_raw_log(reused_dealid_rows, path=path)

        canonicalize_trade_log(path=path)

        trades = load_raw_log(path)
        assert len(trades) == 2, "Distinct historical trades reusing the same dealId must not be merged"


class TestFetchExitFromHistory:
    """history_sync._fetch_exit_from_history must not accept a stale history row
    from a previous, same-dealId trade as the exit for a still-open trade.

    Capital.com reuses account-level dealIds across many trades, so a bare
    dealId match is not proof that a history row is *this* trade's close.
    """

    class _Resp:
        def __init__(self, transactions):
            self.status_code = 200
            self._transactions = transactions

        def json(self):
            return {"transactions": self._transactions}

    def _patch_history(self, monkeypatch, transactions):
        import history_sync
        monkeypatch.setattr(history_sync.session, "request",
                            lambda *a, **k: self._Resp(transactions))

    def test_ignores_close_row_predating_trade_entry(self, monkeypatch):
        """A close row whose closeDate predates the trade's entry belongs to a
        previous, same-dealId trade – not to the still-open one."""
        import history_sync
        self._patch_history(monkeypatch, [
            {"dealId": "STX1", "closeLevel": 100.0, "profitAndLoss": -5.0,
             "closeDate": "2026-09-01T10:00:00Z"},
        ])
        trade = {"time_entered": "2026-09-08T10:00:00Z"}
        ep, pnl, ct = history_sync._fetch_exit_from_history("STX1", trade)
        assert (ep, pnl, ct) == (None, None, None), \
            "Stale close row must not be used as the exit for a trade that is still open"

    def test_accepts_close_row_at_or_after_entry(self, monkeypatch):
        import history_sync
        self._patch_history(monkeypatch, [
            {"dealId": "STX1", "closeLevel": 101.5, "profitAndLoss": 3.0,
             "closeDate": "2026-09-08T12:00:00Z"},
        ])
        trade = {"time_entered": "2026-09-08T10:00:00Z"}
        ep, pnl, ct = history_sync._fetch_exit_from_history("STX1", trade)
        assert ep == 101.5 and pnl == 3.0 and ct == "2026-09-08 12:00:00"

    def test_skips_non_close_rows_and_finds_real_close(self, monkeypatch):
        """Open/update rows sharing the dealId must be skipped in favour of the
        genuine close row that follows them."""
        import history_sync
        self._patch_history(monkeypatch, [
            {"dealId": "STX1", "level": 100.0},  # open/update row, no close evidence
            {"dealId": "STX1", "closeLevel": 101.5, "profitAndLoss": 3.0,
             "closeDate": "2026-09-08T12:00:00Z"},
        ])
        trade = {"time_entered": "2026-09-08T10:00:00Z"}
        ep, pnl, ct = history_sync._fetch_exit_from_history("STX1", trade)
        assert ep == 101.5 and pnl == 3.0

    def test_prior_closed_row_tightens_reference_time(self, monkeypatch):
        """When an earlier same-dealId trade already closed after this trade's
        entry, a history row matching *that* close must not be re-used."""
        import history_sync
        self._patch_history(monkeypatch, [
            {"dealId": "STX1", "closeLevel": 100.0, "profitAndLoss": -5.0,
             "closeDate": "2026-09-05T10:00:00Z"},
        ])
        trade = {"time_entered": "2026-09-01T10:00:00Z"}
        prior = {"time_exited": "2026-09-05 10:00:00"}
        ep, pnl, ct = history_sync._fetch_exit_from_history("STX1", trade, prior)
        assert (ep, pnl, ct) == (None, None, None), \
            "A close row matching an earlier same-dealId trade's exit must not be re-used"


class TestReconcileDoesNotPhantomClose:
    """reconcile_with_positions must not mark a still-open trade CLOSED just
    because its dealId is momentarily missing from the positions snapshot."""

    def test_absent_dealid_is_not_closed_by_reconcile(self, tmp_path):
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": "STX1", "ticker": "STX", "side": "buy",
             "size": 1.2, "entry_price": 100.0},
            path=path,
        )

        # The live positions snapshot contains a *different* position only, so
        # the STX trade's dealId is absent. Reconcile must not fabricate a
        # close from the absence alone.
        live_positions = [{
            "dealId": "OTHER", "ticker": "NVDA", "side": "buy",
            "size": 1.0, "entry_price": 200.0,
        }]
        result = reconcile_with_positions(live_positions, path=path)

        trades = load_raw_log(path)
        stx = next(t for t in trades if t.get("dealId") == "STX1")
        assert stx.get("status") == "OPEN", \
            "A trade whose dealId is absent from one snapshot must not be marked CLOSED"
        assert not result["closed"]

    def test_scaled_in_pending_rows_bind_to_correct_dealids_not_mislabelled_trader(self, tmp_path):
        """Regression for the AMAT duplicate-"Trader" bug: two scale-in
        signals for the same ticker/side that both logged a dealId-less
        pending row (order.py's dealReference-less path) must each bind to
        their own live broker dealId via reconcile, not collapse into one
        match while the other falls through to a brand-new "Trader" row."""
        from trade_log import upsert_open_trade, reconcile_with_positions, load_raw_log
        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        upsert_open_trade(
            {"dealId": None, "dealReference": "REF-45M", "ticker": "AMAT", "side": "short",
             "size": 0.66, "entry_price": 504.84, "timeframe": "45M",
             "trade_source": "tradingview", "origin": "tradingview"},
            path=path,
        )
        upsert_open_trade(
            {"dealId": None, "dealReference": "REF-60M", "ticker": "AMAT", "side": "short",
             "size": 0.66, "entry_price": 504.84, "timeframe": "60M",
             "trade_source": "tradingview", "origin": "tradingview"},
            path=path,
        )

        live_positions = [
            {"dealId": "AMAT-DEAL-1", "epic": "AMAT", "side": "SELL", "size": 0.66, "price": 504.84},
            {"dealId": "AMAT-DEAL-2", "epic": "AMAT", "side": "SELL", "size": 0.66, "price": 504.84},
        ]
        result = reconcile_with_positions(live_positions, path=path)

        assert not result["added"], \
            "Both live positions should bind to the existing pending rows, not create new 'Trader' rows"
        trades = load_raw_log(path)
        amat_trades = [t for t in trades if t.get("ticker") == "AMAT"]
        assert len(amat_trades) == 2
        deal_ids = {t.get("dealId") for t in amat_trades}
        assert deal_ids == {"AMAT-DEAL-1", "AMAT-DEAL-2"}
        for t in amat_trades:
            assert t.get("trade_source") == "tradingview", \
                f"Scaled-in trade should keep its tradingview origin, got {t.get('trade_source')!r}"
    """Webhook orders must create a pending row and then enrich with dealId."""

    class _Response:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}
            self.text = ""

        def json(self):
            return self._body

    def test_logs_pending_order_when_confirmation_has_no_deal_id(self, monkeypatch):
        import order

        appended = []
        monkeypatch.setattr(order.auth, "ensure_token", lambda: True)
        monkeypatch.setattr(order.time, "sleep", lambda _: None)
        monkeypatch.setattr(order, "append_open_trade", lambda payload: appended.append(payload))
        monkeypatch.setattr(order.session, "update_last_trade", lambda: None)
        monkeypatch.setattr(
            order.session,
            "request",
            lambda method, url, **kwargs: (
                self._Response(200, {"snapshot": {"bid": 397.4, "offer": 397.5}})
                if method == "GET" and "/markets/" in url
                else self._Response(200, {"dealReference": "REF-1"})
                if method == "POST"
                else self._Response(404)
            ),
        )

        result = order.place_order("UNH", "buy", 2.05)

        assert result["dealId"] is None
        assert len(appended) == 1
        assert appended[0]["dealReference"] == "REF-1"
        assert appended[0]["dealId"] is None

    def test_logs_order_with_confirmed_deal_id(self, monkeypatch):
        import order

        appended = []
        monkeypatch.setattr(order.auth, "ensure_token", lambda: True)
        monkeypatch.setattr(order.time, "sleep", lambda _: None)
        monkeypatch.setattr(order, "append_open_trade", lambda payload: appended.append(payload) or payload)
        monkeypatch.setattr(order.session, "update_last_trade", lambda: None)
        monkeypatch.setattr(
            order.session,
            "request",
            lambda method, url, **kwargs: (
                self._Response(200, {"snapshot": {"bid": 397.4, "offer": 397.5}})
                if method == "GET" and "/markets/" in url
                else self._Response(200, {"dealReference": "REF-1"})
                if method == "POST"
                else self._Response(200, {"dealStatus": "ACCEPTED", "dealId": "DEAL-1"})
            ),
        )

        order.place_order("UNH", "buy", 2.05)

        assert len(appended) == 2
        assert appended[0]["dealId"] is None
        assert appended[0]["dealReference"] == "REF-1"
        assert appended[1]["dealId"] == "DEAL-1"
        assert appended[1]["dealReference"] == "REF-1"

    def test_logs_pending_order_even_when_no_deal_reference_is_returned(self, monkeypatch):
        """Regression: a broker order response with no dealReference at all
        must still leave a trade_source="tradingview" pending row behind, so
        reconcile_with_positions() can bind the real dealId to it later
        instead of creating a brand-new "Trader"-labelled duplicate row."""
        import order

        appended = []
        monkeypatch.setattr(order.auth, "ensure_token", lambda: True)
        monkeypatch.setattr(order.time, "sleep", lambda _: None)
        monkeypatch.setattr(order, "append_open_trade", lambda payload: appended.append(payload))
        monkeypatch.setattr(order.session, "update_last_trade", lambda: None)
        monkeypatch.setattr(
            order.session,
            "request",
            lambda method, url, **kwargs: (
                self._Response(200, {"snapshot": {"bid": 397.4, "offer": 397.5}})
                if method == "GET" and "/markets/" in url
                else self._Response(200, {})  # POST order response has no dealReference
            ),
        )

        result = order.place_order("UNH", "buy", 2.05, timeframe="15M")

        assert result["dealReference"] is None
        assert result["dealId"] is None
        assert len(appended) == 1
        assert appended[0]["dealId"] is None
        assert appended[0]["dealReference"] is None
        assert appended[0]["ticker"] == "UNH"
        assert appended[0]["trade_source"] == "tradingview"
        assert appended[0]["timeframe"] == "15M"


class TestSyncClosedTradesDisappearanceGuard:
    """sync_closed_trades() must not auto-close from disappearance alone without
    broker close evidence in transaction history."""

    def test_empty_positions_snapshot_still_closes_when_history_confirms_exit(self, tmp_path, monkeypatch):
        import history_sync
        import trade_log

        path = str(tmp_path / "log.json")
        trade_log.save_raw_log([{
            "dealId": "EXIT-1",
            "dealReference": "REF-EXIT-1",
            "ticker": "AAPL",
            "side": "long",
            "size": 1.0,
            "entry_price": 200.0,
            "time_entered": "2026-01-01T00:00:00Z",
            "status": "OPEN",
            "trade_source": "tradingview",
            "origin": "tradingview",
        }], path=path)

        monkeypatch.setattr(history_sync, "canonicalize_trade_log", lambda: trade_log.canonicalize_trade_log(path=path))
        monkeypatch.setattr(history_sync, "load_raw_log", lambda: trade_log.load_raw_log(path=path))
        monkeypatch.setattr(
            history_sync,
            "close_trade_by_dealId",
            lambda deal_id, **kwargs: trade_log.close_trade_by_dealId(deal_id, path=path, **kwargs),
        )
        monkeypatch.setattr(
            history_sync,
            "close_trade_fallback",
            lambda ticker, entry_price, **kwargs: trade_log.close_trade_fallback(ticker, entry_price, path=path, **kwargs),
        )
        monkeypatch.setattr(history_sync.session, "get_positions", lambda: [])
        monkeypatch.setattr(history_sync, "_confirm_position_gone", lambda deal_id: True)
        monkeypatch.setattr(history_sync, "_fetch_exit_from_history", lambda *args, **kwargs: (198.4, -1.6, "2026-09-11 09:30:00"))
        monkeypatch.setattr(history_sync, "get_snapshot", lambda epic: (198.4, 198.5))

        history_sync._last_raw_1 = {"EXIT-1"}
        history_sync._last_raw_2 = set()
        history_sync._last_close_cache = {}
        history_sync._absent_count = {}

        history_sync.sync_closed_trades()

        trades = trade_log.load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["status"] == "CLOSED"
        assert trades[0]["exit_price"] == pytest.approx(198.4)
        assert trades[0]["time_exited"] == "2026-09-11 09:30:00"

    def test_disappearance_without_history_close_evidence_stays_open(self, tmp_path, monkeypatch):
        import history_sync
        import trade_log

        path = str(tmp_path / "log.json")
        trade_log.save_raw_log([{
            "dealId": "STX-LIVE-1",
            "dealReference": "REF-STX-1",
            "ticker": "STX",
            "side": "long",
            "size": 0.2,
            "entry_price": 865.22,
            "time_entered": "2026-01-01T00:00:00Z",
            "status": "OPEN",
            "trade_source": "tradingview",
            "origin": "tradingview",
        }], path=path)

        monkeypatch.setattr(history_sync, "canonicalize_trade_log", lambda: trade_log.canonicalize_trade_log(path=path))
        monkeypatch.setattr(history_sync, "load_raw_log", lambda: trade_log.load_raw_log(path=path))
        monkeypatch.setattr(
            history_sync,
            "close_trade_by_dealId",
            lambda deal_id, **kwargs: trade_log.close_trade_by_dealId(deal_id, path=path, **kwargs),
        )
        monkeypatch.setattr(
            history_sync,
            "close_trade_fallback",
            lambda ticker, entry_price, **kwargs: trade_log.close_trade_fallback(ticker, entry_price, path=path, **kwargs),
        )

        monkeypatch.setattr(
            history_sync.session,
            "get_positions",
            lambda: [{"position": {"dealId": "OTHER-1", "size": 1.0}, "market": {"epic": "US.OTHER"}}],
        )
        monkeypatch.setattr(history_sync, "_confirm_position_gone", lambda deal_id: True)
        monkeypatch.setattr(history_sync, "_fetch_exit_from_history", lambda *args, **kwargs: (None, None, None))
        monkeypatch.setattr(history_sync, "get_snapshot", lambda epic: (863.75, 863.86))

        history_sync._last_raw_1 = set()
        history_sync._last_raw_2 = set()
        history_sync._last_close_cache = {}
        history_sync._absent_count = {}

        for _ in range(3):
            history_sync.sync_closed_trades()

        trades = trade_log.load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["status"] == "OPEN"
        assert trades[0].get("time_exited") in (None, "")

    def test_reused_dealid_with_prior_closed_row_still_closes_new_open_trade(self, tmp_path, monkeypatch):
        import history_sync
        import trade_log

        path = str(tmp_path / "log.json")
        trade_log.save_raw_log([
            {
                "dealId": "REUSED-1",
                "dealReference": "REF-OLD",
                "ticker": "OLD",
                "side": "long",
                "size": 1.0,
                "entry_price": 100.0,
                "time_entered": "2026-09-10T09:00:00Z",
                "time_exited": "2026-09-10T10:00:00Z",
                "status": "CLOSED",
            },
            {
                "dealId": "REUSED-1",
                "dealReference": "REF-NEW",
                "ticker": "INTC",
                "side": "short",
                "size": 1.5,
                "entry_price": 101.0,
                "time_entered": "2026-01-01T09:00:00Z",
                "status": "OPEN",
                "trade_source": "tradingview",
                "origin": "tradingview",
            },
        ], path=path)

        monkeypatch.setattr(history_sync, "canonicalize_trade_log", lambda: trade_log.canonicalize_trade_log(path=path))
        monkeypatch.setattr(history_sync, "load_raw_log", lambda: trade_log.load_raw_log(path=path))
        monkeypatch.setattr(
            history_sync,
            "close_trade_by_dealId",
            lambda deal_id, **kwargs: trade_log.close_trade_by_dealId(deal_id, path=path, **kwargs),
        )
        monkeypatch.setattr(
            history_sync,
            "close_trade_fallback",
            lambda ticker, entry_price, **kwargs: trade_log.close_trade_fallback(ticker, entry_price, path=path, **kwargs),
        )
        monkeypatch.setattr(
            history_sync.session,
            "get_positions",
            lambda: [{"position": {"dealId": "OTHER-1", "size": 1.0}, "market": {"epic": "US.OTHER"}}],
        )
        monkeypatch.setattr(history_sync, "_confirm_position_gone", lambda deal_id: True)
        monkeypatch.setattr(history_sync, "_fetch_exit_from_history", lambda *args, **kwargs: (95.5, -8.25, "2026-09-11 09:30:00"))
        monkeypatch.setattr(history_sync, "get_snapshot", lambda epic: (95.5, 95.6))

        history_sync._last_raw_1 = {"REUSED-1"}
        history_sync._last_raw_2 = set()
        history_sync._last_close_cache = {}
        history_sync._absent_count = {}

        history_sync.sync_closed_trades()

        trades = trade_log.load_raw_log(path)
        new_trade = next(t for t in trades if t.get("dealReference") == "REF-NEW")
        assert new_trade["status"] == "CLOSED"
        assert new_trade["time_exited"] == "2026-09-11 09:30:00"
        assert new_trade["exit_price"] == pytest.approx(95.5)

    def test_closes_with_null_exit_price_when_history_and_snapshot_both_fail(self, tmp_path, monkeypatch):
        """Regression: a broker-confirmed close (size==0) that neither the
        transaction history nor a live market snapshot can supply a price for
        (e.g. a sharp spike between polling ticks) must still be finalised as
        CLOSED, instead of being left stuck OPEN forever. The trader can then
        fill in the correct exit price via the dashboard's edit action."""
        import history_sync
        import trade_log

        path = str(tmp_path / "log.json")
        trade_log.save_raw_log([{
            "dealId": "SPIKE-1",
            "dealReference": "REF-SPIKE-1",
            "ticker": "TSLA",
            "side": "long",
            "size": 1.0,
            "entry_price": 200.0,
            "time_entered": "2026-01-01T00:00:00Z",
            "status": "OPEN",
            "trade_source": "tradingview",
            "origin": "tradingview",
        }], path=path)

        monkeypatch.setattr(history_sync, "canonicalize_trade_log", lambda: trade_log.canonicalize_trade_log(path=path))
        monkeypatch.setattr(history_sync, "load_raw_log", lambda: trade_log.load_raw_log(path=path))
        monkeypatch.setattr(
            history_sync,
            "close_trade_by_dealId",
            lambda deal_id, **kwargs: trade_log.close_trade_by_dealId(deal_id, path=path, **kwargs),
        )
        monkeypatch.setattr(
            history_sync,
            "close_trade_fallback",
            lambda ticker, entry_price, **kwargs: trade_log.close_trade_fallback(ticker, entry_price, path=path, **kwargs),
        )
        # Broker reports the position with size==0 (a direct, non-disappearance
        # close signal), so no _confirm_position_gone check is even involved.
        monkeypatch.setattr(
            history_sync.session,
            "get_positions",
            lambda: [{"position": {"dealId": "SPIKE-1", "size": 0}, "market": {"epic": "US.TSLA"}}],
        )
        monkeypatch.setattr(history_sync, "_fetch_exit_from_history", lambda *args, **kwargs: (None, None, None))
        monkeypatch.setattr(history_sync, "get_snapshot", lambda epic: (None, None))

        history_sync._last_raw_1 = set()
        history_sync._last_raw_2 = set()
        history_sync._last_close_cache = {}
        history_sync._absent_count = {}

        history_sync.sync_closed_trades()

        trades = trade_log.load_raw_log(path)
        assert len(trades) == 1
        assert trades[0]["status"] == "CLOSED"
        assert trades[0].get("exit_price") is None
        assert trades[0].get("pnl") is None


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
        # 0 wins, 4 losses -> win rate 0.0, floored at TICKER_EQUITY_SCALE_MIN.
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "AMD", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -10.0}
            for _ in range(4)
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="AMD", ticker="AMD")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(0.3)
        assert result["equity_used"] == pytest.approx(60.0)

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
        # 3 wins, 2 losses -> 60% win rate.
        monkeypatch.setattr(sizing, "load_raw_log", lambda: [
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": 10.0},
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": 10.0},
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": 10.0},
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -5.0},
            {"ticker": "TSLA", "status": "CLOSED", "trade_source": "tradingview", "pnl_gbp": -5.0},
        ])

        result = sizing.calculate_size(100, 95, 110, "buy", symbol="TSLA", ticker="TSLA")

        assert result["blocked"] is False
        assert result["ticker_equity_scale"] == pytest.approx(0.6)
        assert result["equity_used"] == pytest.approx(120.0)

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


class TestDeleteCompletedTrade:
    def test_deletes_only_selected_completed_trade(self, tmp_path):
        from trade_log import delete_completed_trade, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([
                {"dealId": "OPEN1", "ticker": "AAPL", "status": "OPEN"},
                {"dealId": "CLOSED1", "ticker": "NVDA", "status": "CLOSED", "time_exited": "2026-09-09T10:00:00Z"},
                {"dealId": "CLOSED2", "ticker": "TSLA", "status": "CLOSED", "time_exited": "2026-09-09T11:00:00Z"},
            ], f)

        ok, deleted, status = delete_completed_trade(1, path=path)
        trades = load_raw_log(path)

        assert ok is True
        assert status == "deleted"
        assert deleted["dealId"] == "CLOSED1"
        assert [t["dealId"] for t in trades] == ["OPEN1", "CLOSED2"]

    def test_rejects_open_trade_deletion(self, tmp_path):
        from trade_log import delete_completed_trade, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{"dealId": "OPEN1", "ticker": "AAPL", "status": "OPEN"}], f)

        ok, deleted, status = delete_completed_trade(0, path=path)
        trades = load_raw_log(path)

        assert ok is False
        assert deleted is None
        assert status == "not_completed"
        assert len(trades) == 1


class TestDeleteTradeLogEntry:
    def test_deletes_open_tradingview_trade_when_broker_confirms_missing(self, tmp_path, monkeypatch):
        from trade_log import delete_trade_log_entry, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "PHANTOM1",
                "dealReference": "REF-PHANTOM1",
                "ticker": "AMZN",
                "status": "OPEN",
                "trade_source": "tradingview",
                "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-PHANTOM1",
            }], f)

        class _Resp:
            status_code = 404
            text = ""

            def json(self):
                return {}

        monkeypatch.setattr("session.request", lambda *_args, **_kwargs: _Resp())

        ok, deleted, status = delete_trade_log_entry(0, path=path)
        trades = load_raw_log(path)

        assert ok is True
        assert status == "deleted"
        assert deleted["dealId"] == "PHANTOM1"
        assert trades == []

    def test_rejects_open_trade_deletion_when_broker_still_reports_position(self, tmp_path, monkeypatch):
        from trade_log import delete_trade_log_entry, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "LIVE1",
                "dealReference": "REF-LIVE1",
                "ticker": "AMZN",
                "status": "OPEN",
                "trade_source": "tradingview",
                "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-LIVE1",
            }], f)

        class _Resp:
            status_code = 200

            def json(self):
                return {"dealId": "LIVE1"}

        monkeypatch.setattr("session.request", lambda *_args, **_kwargs: _Resp())

        ok, deleted, status = delete_trade_log_entry(0, path=path)
        trades = load_raw_log(path)

        assert ok is False
        assert deleted is None
        assert status == "broker_still_open"
        assert len(trades) == 1

    def test_deletes_open_tradingview_trade_when_broker_returns_400_not_found(self, tmp_path, monkeypatch):
        from trade_log import delete_trade_log_entry, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "PHANTOM400",
                "dealReference": "REF-PHANTOM400",
                "ticker": "MSFT",
                "status": "OPEN",
                "trade_source": "tradingview",
                "notes": "sl=1; tp=2; timeframe=1h; dealReference=REF-PHANTOM400",
            }], f)

        class _Resp:
            status_code = 400
            text = "position not found"

            def json(self):
                return {"errorCode": "position_not_found"}

        monkeypatch.setattr("session.request", lambda *_args, **_kwargs: _Resp())

        ok, deleted, status = delete_trade_log_entry(0, path=path)
        trades = load_raw_log(path)

        assert ok is True
        assert status == "deleted"
        assert deleted["dealId"] == "PHANTOM400"
        assert trades == []

    def test_deletes_open_tradingview_trade_with_trusted_origin_marker(self, tmp_path, monkeypatch):
        from trade_log import delete_trade_log_entry, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "PHANTOM-TRUSTED",
                "ticker": "META",
                "status": "OPEN",
                "trade_source": "tradingview",
                "trusted_origin": "tradingview",
            }], f)

        class _Resp:
            status_code = 404
            text = ""

            def json(self):
                return {}

        monkeypatch.setattr("session.request", lambda *_args, **_kwargs: _Resp())

        ok, deleted, status = delete_trade_log_entry(0, path=path)
        trades = load_raw_log(path)

        assert ok is True
        assert status == "deleted"
        assert deleted["dealId"] == "PHANTOM-TRUSTED"
        assert trades == []


class TestUpdateTradeExitPriceEntry:
    """Tests for trade_log.update_trade_exit_price_entry."""

    def test_sets_exit_price_and_recomputes_pnl_for_closed_trade(self, tmp_path):
        from trade_log import update_trade_exit_price_entry, load_raw_log

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "D1",
                "ticker": "AAPL",
                "side": "long",
                "size": 10,
                "entry_price": 100.0,
                "exit_price": None,
                "pnl": None,
                "pnl_gbp": None,
                "status": "CLOSED",
                "time_exited": "2026-10-05T19:00:00Z",
            }], f)

        ok, updated, status = update_trade_exit_price_entry(0, 110, path=path)

        assert ok is True
        assert status == "updated"
        assert updated["exit_price"] == 110.0
        assert updated["pnl"] == pytest.approx(100.0)
        assert updated["pnl_gbp"] is not None

        trades = load_raw_log(path)
        assert trades[0]["exit_price"] == 110.0
        assert trades[0]["pnl"] == pytest.approx(100.0)

    def test_rejects_edit_for_still_open_trade(self, tmp_path):
        from trade_log import update_trade_exit_price_entry

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "D1",
                "ticker": "AAPL",
                "side": "long",
                "size": 10,
                "entry_price": 100.0,
                "status": "OPEN",
            }], f)

        ok, updated, status = update_trade_exit_price_entry(0, 110, path=path)

        assert ok is False
        assert updated is None
        assert status == "not_completed"

    def test_rejects_invalid_price(self, tmp_path):
        from trade_log import update_trade_exit_price_entry

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([{
                "dealId": "D1",
                "ticker": "AAPL",
                "status": "CLOSED",
                "time_exited": "2026-10-05T19:00:00Z",
            }], f)

        ok, updated, status = update_trade_exit_price_entry(0, "not-a-number", path=path)
        assert ok is False and status == "invalid_price"

        ok, updated, status = update_trade_exit_price_entry(0, -5, path=path)
        assert ok is False and status == "invalid_price"

    def test_rejects_out_of_range_index(self, tmp_path):
        from trade_log import update_trade_exit_price_entry

        path = str(tmp_path / "log.json")
        with open(path, "w") as f:
            json.dump([], f)

        ok, updated, status = update_trade_exit_price_entry(0, 110, path=path)
        assert ok is False
        assert updated is None
        assert status == "not_found"


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

    def test_same_ticker_signal_is_blocked_when_sizing_capacity_is_exhausted(self, monkeypatch):
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

        monkeypatch.setattr(dashboard.session, "get_positions", lambda: [])
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


class TestDashboardRoles:
    def test_owner_login_sets_owner_role_cookie(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard
        import dashboard as dashboard_module

        monkeypatch.delenv("DASHBOARD_OWNER_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_VIEWER_PASSWORD", raising=False)
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_OWNER_PASSWORD", "owner-pw")
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_VIEWER_PASSWORD", "viewer-pw")

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()

        response = client.post("/dashboard/login", data={"username": "owner", "password": "owner-pw"})

        assert response.status_code == 302
        assert response.headers["Location"] == "/dashboard"
        set_cookie_headers = response.headers.get_all("Set-Cookie")
        assert any("dashboard_auth=1" in h for h in set_cookie_headers)
        assert any("dashboard_role=owner" in h for h in set_cookie_headers)

    def test_viewer_login_sets_viewer_role_cookie(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard
        import dashboard as dashboard_module

        monkeypatch.delenv("DASHBOARD_OWNER_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_VIEWER_PASSWORD", raising=False)
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_OWNER_PASSWORD", "owner-pw")
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_VIEWER_PASSWORD", "viewer-pw")

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()

        response = client.post("/dashboard/login", data={"username": "viewer", "password": "viewer-pw"})

        assert response.status_code == 302
        set_cookie_headers = response.headers.get_all("Set-Cookie")
        assert any("dashboard_role=viewer" in h for h in set_cookie_headers)

    def test_viewer_password_rejected_for_owner_username(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard
        import dashboard as dashboard_module

        monkeypatch.delenv("DASHBOARD_OWNER_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_VIEWER_PASSWORD", raising=False)
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_OWNER_PASSWORD", "owner-pw")
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_VIEWER_PASSWORD", "viewer-pw")

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()

        response = client.post("/dashboard/login", data={"username": "owner", "password": "viewer-pw"})

        assert response.status_code == 401
        assert "Invalid" in response.get_data(as_text=True)

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

    def test_investor_login_sets_investor_role_cookie(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard
        import dashboard as dashboard_module

        monkeypatch.delenv("DASHBOARD_OWNER_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_INVESTOR_PASSWORD", raising=False)
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_OWNER_PASSWORD", "owner-pw")
        monkeypatch.setattr(dashboard_module.config, "DASHBOARD_INVESTOR_PASSWORD", "investor-pw")

        app = Flask(__name__)
        app.register_blueprint(dashboard)
        client = app.test_client()

        response = client.post("/dashboard/login", data={"username": "investor", "password": "investor-pw"})

        assert response.status_code == 302
        set_cookie_headers = response.headers.get_all("Set-Cookie")
        assert any("dashboard_role=investor" in h for h in set_cookie_headers)

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
        monkeypatch.setattr(dashboard.session, "get_positions", lambda: live_positions)
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

        monkeypatch.setattr(dashboard.session, "get_positions", lambda: None)
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

        monkeypatch.setattr(dashboard.session, "get_positions", lambda: [{"market": {"symbol": "MSFT"}}])
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


class TestInvestorTiers:
    """Tests for deposits.py's investor ROI tier store."""

    def test_default_tier_is_tradingview_when_unset(self, tmp_path):
        import deposits
        path = str(tmp_path / "tiers.json")
        assert deposits.get_investor_tier("Bob", path=path) == "tradingview"

    def test_set_and_get_tier_round_trips_case_insensitively(self, tmp_path):
        import deposits
        path = str(tmp_path / "tiers.json")
        ok, status = deposits.set_investor_tier("Carol", "full", path=path)
        assert ok is True
        assert status == "updated"
        assert deposits.get_investor_tier("CAROL", path=path) == "full"
        assert deposits.get_investor_tier("carol  ", path=path) == "full"

    def test_set_tier_rejects_invalid_tier_name(self, tmp_path):
        import deposits
        path = str(tmp_path / "tiers.json")
        ok, status = deposits.set_investor_tier("Carol", "platinum", path=path)
        assert ok is False
        assert status == "invalid_tier"

    def test_set_tier_rejects_empty_investor_name(self, tmp_path):
        import deposits
        path = str(tmp_path / "tiers.json")
        ok, status = deposits.set_investor_tier("   ", "full", path=path)
        assert ok is False
        assert status == "invalid_investor"


class TestBalanceHistoryPeriodReturns:
    """Tests for deposits.py's calendar-aligned balance-history snapshots
    and the Daily/Weekly/Monthly Return % they feed, used by the Dashboard's
    Return card (distinct from the rolling-window weekly/monthly analytics)."""

    def test_record_daily_balance_snapshot_is_idempotent_per_day(self, tmp_path):
        import deposits
        path = str(tmp_path / "history.json")
        deposits.record_daily_balance_snapshot(1000.0, path=path)
        deposits.record_daily_balance_snapshot(2000.0, path=path)  # same day: no-op
        history = deposits._load_balance_history(path)
        assert len(history) == 1
        assert history[0]["balance"] == 1000.0

    def test_record_daily_balance_snapshot_ignores_none_balance(self, tmp_path):
        import deposits
        path = str(tmp_path / "history.json")
        deposits.record_daily_balance_snapshot(None, path=path)
        assert deposits._load_balance_history(path) == []

    def test_compute_period_returns_with_no_history_returns_all_none(self, tmp_path):
        import deposits
        path = str(tmp_path / "history.json")
        result = deposits.compute_period_returns(1000.0, path=path)
        assert result == {
            "daily": None, "weekly": None, "monthly": None,
            "daily_opening": None, "weekly_opening": None, "monthly_opening": None,
        }

    def test_compute_period_returns_computes_each_calendar_period(self, tmp_path):
        import deposits
        from datetime import date, timedelta
        path = str(tmp_path / "history.json")
        today = date.today()
        history = [
            {"date": (today - timedelta(days=40)).isoformat(), "balance": 1000.0},
            {"date": today.replace(day=1).isoformat(), "balance": 1100.0},
            {"date": (today - timedelta(days=today.weekday())).isoformat(), "balance": 1150.0},
            {"date": today.isoformat(), "balance": 1180.0},
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)

        result = deposits.compute_period_returns(1200.0, path=path)

        assert result["daily_opening"] == 1180.0
        assert result["weekly_opening"] == 1150.0
        assert result["monthly_opening"] == 1100.0
        assert result["daily"] == pytest.approx(round((1200.0 - 1180.0) / 1180.0 * 100, 2))
        assert result["weekly"] == pytest.approx(round((1200.0 - 1150.0) / 1150.0 * 100, 2))
        assert result["monthly"] == pytest.approx(round((1200.0 - 1100.0) / 1100.0 * 100, 2))

    def test_compute_period_returns_falls_back_to_earliest_snapshot_in_period(self, tmp_path):
        """If tracking only started mid-month (no snapshot on the 1st), the
        earliest snapshot within the month is used as the opening balance
        rather than showing no Return at all."""
        import deposits
        from datetime import date
        path = str(tmp_path / "history.json")
        today = date.today()
        mid_month = today.replace(day=min(today.day, 28))
        history = [{"date": mid_month.isoformat(), "balance": 900.0}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)

        result = deposits.compute_period_returns(990.0, path=path)

        assert result["monthly_opening"] == 900.0
        assert result["monthly"] == 10.0

    def test_compute_period_returns_handles_none_balance(self, tmp_path):
        import deposits
        path = str(tmp_path / "history.json")
        deposits.record_daily_balance_snapshot(1000.0, path=path)
        result = deposits.compute_period_returns(None, path=path)
        assert result["daily"] is None


class TestInvestorBreakdownOwnerFeeOverride:
    """Tests for the owner_override_pct_override parameter added to
    deposits.investor_breakdown() for the Trader ROI sleeve (no performance
    fee should apply there)."""

    def test_override_zero_disables_owner_fee_on_profitable_trade(self):
        import deposits
        entries = [
            {"type": "deposit", "amount": 1000, "investor": "Aleks", "occurred_at": "2026-01-01"},
            {"type": "deposit", "amount": 1000, "investor": "Carol", "occurred_at": "2026-01-01"},
        ]
        pnl_events = [{"ts": "2026-01-02T00:00:00", "pnl": 200.0}]
        result = deposits.investor_breakdown(entries, pnl_events, owner_override_pct_override=0)
        assert result["owner_override_pct"] == 0
        by_name = {i["investor"]: i for i in result["investors"]}
        # With no fee, the 200 profit splits evenly 50/50 by equal capital.
        assert by_name["Aleks"]["allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)
        assert by_name["Carol"]["allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)

    def test_tracked_total_is_always_exposed(self):
        import deposits
        entries = [{"type": "deposit", "amount": 500, "investor": "Aleks", "occurred_at": "2026-01-01"}]
        result = deposits.investor_breakdown(entries, [], current_balance=None)
        assert result["tracked_total"] == 500.0


class TestRoiTierBreakdown:
    """Tests for dashboard._build_roi_breakdown's two-sleeve ROI split:
    every investor shares TradingView/Hedge PnL, but only the Owner and
    "full" tier investors additionally share Trader (discretionary) PnL,
    with the real account balance reconciling exactly across both sleeves."""

    def _entries(self):
        return [
            {"type": "deposit", "amount": 1000, "investor": "Aleks", "occurred_at": "2026-01-01"},
            {"type": "deposit", "amount": 1000, "investor": "Bob", "occurred_at": "2026-01-02"},
            {"type": "deposit", "amount": 1000, "investor": "Carol", "occurred_at": "2026-01-02"},
        ]

    def _trades(self):
        return [
            {"status": "CLOSED", "trade_source": "TradingView", "pnl_gbp": 300, "time_exited": "2026-01-03T00:00:00Z"},
            {"status": "CLOSED", "trade_source": "Trader", "pnl_gbp": 200, "time_exited": "2026-01-03T00:00:00Z"},
        ]

    def test_tradingview_tier_investor_excluded_from_trader_pnl(self, monkeypatch, tmp_path):
        import dashboard, deposits
        monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))

        breakdown = dashboard._build_roi_breakdown(self._entries(), self._trades(), balance=3500)
        by_name = {i["investor"]: i for i in breakdown["investors"]}

        assert by_name["Bob"]["tier"] == "tradingview"
        assert by_name["Bob"]["trader_allocated_gain_loss"] is None
        # Bob's gain/loss comes only from the TradingView sleeve.
        assert by_name["Bob"]["allocated_gain_loss"] == pytest.approx(85.2, abs=0.01)

    def test_full_tier_investor_shares_trader_pnl_pro_rata(self, monkeypatch, tmp_path):
        import dashboard, deposits
        monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))
        deposits.set_investor_tier("Carol", "full")

        breakdown = dashboard._build_roi_breakdown(self._entries(), self._trades(), balance=3500)
        by_name = {i["investor"]: i for i in breakdown["investors"]}

        assert by_name["Carol"]["tier"] == "full"
        # Trader sleeve is split pro-rata between Aleks and Carol only (1000 each).
        assert by_name["Carol"]["trader_allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)
        assert by_name["Aleks"]["trader_allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)
        assert by_name["Aleks"]["tier"] == "owner"

    def test_owner_receives_no_trader_sleeve_performance_fee(self, monkeypatch, tmp_path):
        """The 15% Owner performance fee applies only to the TradingView
        sleeve; the Trader sleeve is split purely pro-rata."""
        import dashboard, deposits
        monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))
        deposits.set_investor_tier("Carol", "full")

        breakdown = dashboard._build_roi_breakdown(self._entries(), self._trades(), balance=3500)
        by_name = {i["investor"]: i for i in breakdown["investors"]}
        # Trader sleeve: 200 profit split evenly between Aleks and Carol
        # (1000 capital each) with no fee skimmed off the top.
        assert by_name["Aleks"]["trader_allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)
        assert by_name["Carol"]["trader_allocated_gain_loss"] == pytest.approx(100.0, abs=0.01)

    def test_combined_figures_reconcile_exactly_to_real_balance(self, monkeypatch, tmp_path):
        """Regardless of tier mix, the sum of every investor's combined
        current_value/allocated_gain_loss must reconcile exactly to the
        real account balance/overall gain-loss (no double-counted capital)."""
        import dashboard, deposits
        monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))
        deposits.set_investor_tier("Carol", "full")

        breakdown = dashboard._build_roi_breakdown(self._entries(), self._trades(), balance=3500)

        assert breakdown["reconciliation_adjustment"] in (0, 0.0, None) or abs(breakdown["reconciliation_adjustment"]) < 0.01
        total_current_value = sum(i["current_value"] for i in breakdown["investors"])
        total_allocated = sum(i["allocated_gain_loss"] for i in breakdown["investors"])
        assert total_current_value == pytest.approx(3500.0, abs=0.01)
        assert total_allocated == pytest.approx(500.0, abs=0.01)

    def test_hedge_trades_are_shared_by_every_investor(self, monkeypatch, tmp_path):
        """Hedge-labelled trades count as automated/TradingView-sleeve PnL,
        so TradingView-tier investors (not just Owner/full-tier) share it."""
        import dashboard, deposits
        monkeypatch.setattr(deposits, "TIERS_PATH", str(tmp_path / "tiers.json"))

        trades = [{"status": "CLOSED", "trade_source": "hedge", "pnl_gbp": 300, "time_exited": "2026-01-03T00:00:00Z"}]
        breakdown = dashboard._build_roi_breakdown(self._entries(), trades, balance=3300)
        by_name = {i["investor"]: i for i in breakdown["investors"]}
        assert by_name["Bob"]["tv_allocated_gain_loss"] == pytest.approx(85.2, abs=0.01)

