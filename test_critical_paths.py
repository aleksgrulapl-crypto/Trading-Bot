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

    def test_live_position_import_defaults_to_manual(self, tmp_path):
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
        assert trades[0]["trade_source"] == "manual"

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
    """Webhook orders must not create a log row before broker confirmation."""

    class _Response:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}
            self.text = ""

        def json(self):
            return self._body

    def test_does_not_log_order_when_confirmation_has_no_deal_id(self, monkeypatch):
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
        assert appended == []

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

        assert len(appended) == 1
        assert appended[0]["dealId"] == "DEAL-1"
        assert appended[0]["dealReference"] == "REF-1"


class TestSyncClosedTradesDisappearanceGuard:
    """sync_closed_trades() must not auto-close from disappearance alone without
    broker close evidence in transaction history."""

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
                "time_entered": "2026-09-11T09:00:00Z",
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
            {"trade_source": "hedge"},
            {"notes": "Imported from webhook (legacy)"},
            {"trade_source": "unknown"},
        ])
        assert [t["trade_type"] for t in trades] == [
            "TradingView",
            "TradingView",
            "Manual",
            "Hedge",
            "TradingView",
            "Manual",
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
                "trade_source": "manual",
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
        assert any(t["status"] == "OPEN" and t["trade_type"] == "Manual" for t in ctx["combined_trades"])
        assert any(t["status"] == "CLOSED" and t["trade_type"] == "TradingView" for t in ctx["combined_trades"])
        assert ctx["analytics"]["trade_count"] == 1


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


class TestDashboardDeleteTradeEndpoint:
    def test_delete_endpoint_calls_trade_log_service(self, monkeypatch):
        from flask import Flask
        from dashboard import dashboard

        called = {}

        def _fake_delete(trade_index):
            called["trade_index"] = trade_index
            return True, {"dealId": "D1"}, "deleted"

        monkeypatch.setattr("dashboard.delete_completed_trade", _fake_delete)

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

        monkeypatch.setattr(dashboard.session, "get_positions", lambda: [])
        monkeypatch.setattr(dashboard.session, "get_account", lambda: {})
        monkeypatch.setattr(dashboard.session, "enrich_positions", lambda raw: [])
        monkeypatch.setattr(dashboard.session, "enrich_account", lambda raw: {})
        monkeypatch.setattr(dashboard, "reconcile_with_positions", lambda positions: {"closed": [], "added": [], "reopened": []})
        monkeypatch.setattr(dashboard, "load_raw_log", lambda: [
            {"dealId": "OPEN1", "ticker": "AAPL", "status": "OPEN", "pnl_gbp": None},
            {"dealId": "CLOSED1", "ticker": "NVDA", "status": "CLOSED", "time_exited": "2026-09-09T12:00:00Z", "pnl_gbp": 5.0},
        ])

        app = Flask(__name__)
        app.register_blueprint(dashboard.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")

        response = client.get("/dashboard/data")
        data = response.get_json()
        html = data["html"]

        assert response.status_code == 200
        assert "deleteTrade(1)" in html
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
