"""Tests for trade_log: upsert, reconcile, close, sync, delete."""

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
        assert ep == 101.5 and pnl == 3.0 and ct == "2026-09-08T13:00:00+01:00"

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
