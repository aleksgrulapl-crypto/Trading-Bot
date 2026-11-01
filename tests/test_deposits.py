"""Tests for investors, balance history, withdrawals, ROI."""

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

    def test_compute_period_returns_computes_each_calendar_period(self, tmp_path, monkeypatch):
        import deposits
        from datetime import date, datetime, timedelta
        path = str(tmp_path / "history.json")
        # Fixed mid-week, mid-month "now" so day/week/month boundaries are distinct.
        fixed_now = datetime(2026, 10, 14, 12, 0)
        monkeypatch.setattr(deposits, "_now_naive_uk", lambda: fixed_now)
        today = fixed_now.date()
        history = [
            {"date": (today - timedelta(days=40)).isoformat(), "balance": 1000.0},
            {"date": today.replace(day=1).isoformat(), "balance": 1100.0},
            {"date": (today - timedelta(days=today.weekday())).isoformat(), "balance": 1150.0},
            {"date": today.isoformat(), "balance": 1180.0},
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)

        ev = [{"ts": fixed_now, "pnl": 20.0}]
        result = deposits.compute_period_returns(1200.0, path=path, pnl_events=ev)

        assert result["daily_opening"] == 1180.0
        assert result["weekly_opening"] == 1150.0
        assert result["monthly_opening"] == 1100.0
        assert result["daily"] == pytest.approx(round(20.0 / 1180.0 * 100, 2))
        assert result["weekly"] == pytest.approx(round(20.0 / 1150.0 * 100, 2))
        assert result["monthly"] == pytest.approx(round(20.0 / 1100.0 * 100, 2))

    def test_compute_period_returns_falls_back_to_earliest_snapshot_in_period(self, tmp_path):
        """If tracking only started mid-month (no snapshot on the 1st), the
        earliest snapshot within the month is used as the opening balance
        rather than showing no Return at all."""
        import deposits
        from datetime import date, datetime
        path = str(tmp_path / "history.json")
        today = date.today()
        mid_month = today.replace(day=min(today.day, 28))
        history = [{"date": mid_month.isoformat(), "balance": 900.0}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)

        result = deposits.compute_period_returns(990.0, path=path, pnl_events=[{"ts": datetime.now(), "pnl": 90.0}])

        assert result["monthly_opening"] == 900.0
        assert result["monthly"] == 10.0

    def test_compute_period_returns_handles_none_balance(self, tmp_path):
        import deposits
        path = str(tmp_path / "history.json")
        deposits.record_daily_balance_snapshot(1000.0, path=path)
        result = deposits.compute_period_returns(None, path=path)
        assert result["daily"] is None

    def test_weekly_return_goal_defaults_to_first_milestone_when_no_data(self):
        import deposits
        result = deposits.compute_weekly_return_goal_progress(None)
        assert result == {"goal_pct": 5.0, "progress_pct": 0.0}

    def test_weekly_return_goal_progress_toward_first_milestone(self):
        import deposits
        result = deposits.compute_weekly_return_goal_progress(3.0)
        assert result == {"goal_pct": 5.0, "progress_pct": 60.0}

    def test_weekly_return_goal_steps_up_once_milestone_passed(self):
        import deposits
        result = deposits.compute_weekly_return_goal_progress(7.0)
        assert result == {"goal_pct": 10.0, "progress_pct": 70.0}

    def test_weekly_return_goal_handles_exact_milestone_hit(self):
        import deposits
        result = deposits.compute_weekly_return_goal_progress(5.0)
        assert result == {"goal_pct": 10.0, "progress_pct": 50.0}

    def test_weekly_return_goal_clamps_negative_return_to_zero_progress(self):
        import deposits
        result = deposits.compute_weekly_return_goal_progress(-3.0)
        assert result == {"goal_pct": 5.0, "progress_pct": 0.0}

    def test_successful_weeks_counts_weeks_at_or_above_5_pct_and_averages(self, tmp_path):
        import deposits
        from datetime import datetime
        path = str(tmp_path / "history.json")
        closes = str(tmp_path / "closes.json")
        history = [
            {"date": "2026-09-28", "balance": 1000.0},  # week 1 open
            {"date": "2026-10-05", "balance": 1060.0},  # week 1 close (+6%), week 2 open
            {"date": "2026-10-12", "balance": 1081.2},  # week 2 close (+2%), week 3 open
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)
        now = datetime(2026, 10, 12, 10, 0)  # Monday: week 3 still in progress

        pnl = [
            {"ts": datetime(2026, 9, 30, 12, 0), "pnl": 60.0},
            {"ts": datetime(2026, 10, 7, 12, 0), "pnl": 21.2},
        ]
        result = deposits.compute_successful_weeks(path=path, closes_path=closes, now=now, pnl_events=pnl)

        assert result == {"successful_weeks": 1, "total_weeks": 2, "avg_weekly_return": 4.0}

    def test_week_closes_only_after_friday_21_and_is_frozen(self, tmp_path):
        import deposits
        from datetime import datetime
        path = str(tmp_path / "history.json")
        closes = str(tmp_path / "closes.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"date": "2026-10-05", "balance": 1000.0}], f)

        deposits.record_weekly_close(1100.0, path=closes, now=datetime(2026, 10, 9, 20, 59))
        assert deposits.compute_successful_weeks(path=path, closes_path=closes, now=datetime(2026, 10, 9, 20, 59))["total_weeks"] == 0

        deposits.record_weekly_close(1100.0, path=closes, now=datetime(2026, 10, 9, 21, 0))
        deposits.record_weekly_close(1500.0, path=closes, now=datetime(2026, 10, 10, 9, 0))  # frozen: ignored
        pnl = [{"ts": datetime(2026, 10, 7, 12, 0), "pnl": 100.0}]
        result = deposits.compute_successful_weeks(path=path, closes_path=closes, now=datetime(2026, 10, 10, 9, 0), pnl_events=pnl)
        assert result == {"successful_weeks": 1, "total_weeks": 1, "avg_weekly_return": 10.0}

    def test_successful_weeks_with_no_history(self, tmp_path):
        import deposits
        result = deposits.compute_successful_weeks(path=str(tmp_path / "h.json"), closes_path=str(tmp_path / "c.json"))
        assert result == {"successful_weeks": 0, "total_weeks": 0, "avg_weekly_return": None}

    def test_last_completed_week_and_month_exclude_the_current_period(self, tmp_path):
        import deposits
        from datetime import datetime
        path = str(tmp_path / "history.json")
        closes = str(tmp_path / "closes.json")
        history = [
            {"date": "2026-09-28", "balance": 1000.0},
            {"date": "2026-10-05", "balance": 1060.0},
            {"date": "2026-10-12", "balance": 1081.2},
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f)
        now = datetime(2026, 10, 14, 10, 0)  # week of 12 Oct is still in progress
        pnl = [
            {"ts": datetime(2026, 9, 30, 12, 0), "pnl": 60.0},
            {"ts": datetime(2026, 10, 7, 12, 0), "pnl": 21.2},
            {"ts": datetime(2026, 10, 13, 12, 0), "pnl": 500.0},  # current week: must not leak
        ]
        week = deposits.compute_last_completed_week(path=path, closes_path=closes, now=now, pnl_events=pnl)
        assert week == {"label": "05/10/26 - 09/10/26", "return_pct": 2.0}

        month = deposits.compute_last_completed_month(path=path, now=now, pnl_events=pnl)
        assert month == {"label": "September 2026", "return_pct": 6.0}

    def test_last_completed_month_none_without_history(self, tmp_path):
        import deposits
        assert deposits.compute_last_completed_month(path=str(tmp_path / "h.json")) is None
        assert deposits.compute_last_completed_week(path=str(tmp_path / "h.json"), closes_path=str(tmp_path / "c.json")) is None


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



class TestBackendEntries:
    def _e(self, t, amt, inv, ts):
        return {"type": t, "amount": amt, "investor": inv, "occurred_at": ts, "created_at": ts}

    def test_backend_withdrawal_scales_every_investor_pro_rata(self):
        import deposits
        entries = [
            self._e("deposit", 600.0, "A", "2026-01-01T00:00:00"),
            self._e("deposit", 400.0, "B", "2026-01-01T00:00:00"),
            self._e("withdrawal", 100.0, "backend", "2026-01-02T00:00:00"),
        ]
        r = deposits.investor_breakdown(entries, [], owner_override_pct_override=0)
        by = {i["investor"]: i for i in r["investors"]}
        assert set(by) == {"A", "B"}
        assert by["A"]["current_value"] == 540.0
        assert by["B"]["current_value"] == 360.0
        assert by["A"]["net_contribution"] == 600.0
        assert by["A"]["allocated_gain_loss"] == -60.0

    def test_backend_deposit_adds_units_pro_rata(self):
        import deposits
        entries = [
            self._e("deposit", 600.0, "A", "2026-01-01T00:00:00"),
            self._e("deposit", 400.0, "B", "2026-01-01T00:00:00"),
            self._e("deposit", 100.0, "Backend", "2026-01-02T00:00:00"),
        ]
        r = deposits.investor_breakdown(entries, [], owner_override_pct_override=0)
        by = {i["investor"]: i for i in r["investors"]}
        assert by["A"]["current_value"] == 660.0
        assert by["B"]["current_value"] == 440.0

    def test_backend_excluded_from_investor_list_totals_and_pledges(self):
        import deposits
        entries = [
            self._e("deposit", 100.0, "A", "2026-01-01T00:00:00"),
            self._e("withdrawal", 10.0, "Backend", "2026-01-02T00:00:00"),
        ]
        assert deposits.list_investors(entries) == ["A"]
        s = deposits.summarize(entries)
        assert s["total_withdrawals"] == 0 and s["backend_net_cost"] == 10.0
        assert deposits.add_pledge("BACKEND", 100)[0] is False

    def test_period_returns_ignore_withdrawals(self, tmp_path):
        import deposits
        from datetime import datetime
        today = deposits._now_naive_uk().date().isoformat()
        path = str(tmp_path / "h.json")
        import json
        json.dump([{"date": today, "balance": 1000.0, "recorded_at": today + "T00:00:01"}], open(path, "w"))
        result = deposits.compute_period_returns(910.0, path=path, pnl_events=[])
        assert result["daily"] == 0.0
        ev = [{"ts": deposits._now_naive_uk(), "pnl": 10.0}]
        result = deposits.compute_period_returns(910.0, path=path, pnl_events=ev)
        assert result["daily"] == 1.0


class TestWithdrawalRequests:
    @pytest.fixture
    def paths(self, tmp_path, monkeypatch):
        import deposits
        monkeypatch.setattr(deposits, "LOG_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setattr(deposits, "WITHDRAWALS_PATH", str(tmp_path / "wd.json"))
        monkeypatch.setattr(deposits.config, "OWNER_INVESTOR_NAME", "Aleks", raising=False)
        return deposits

    def _deposit(self, dep, name, amount, days_ago):
        when = (dep._now_naive_uk() - __import__("datetime").timedelta(days=days_ago)).isoformat()
        ok, _e, _s = dep.add_entry("deposit", amount, name, "", occurred_at=when, path=dep.LOG_PATH)
        assert ok

    def test_deposit_is_locked_for_30_days(self, paths):
        dep = paths
        self._deposit(dep, "Jane", 200, 10)
        ok, _r, status = dep.add_withdrawal_request("jane", 50)
        assert (ok, status) == (False, "funds_locked")
        self._deposit(dep, "Jane", 100, 40)
        assert dep.add_withdrawal_request("jane", 150)[2] == "amount_exceeds_unlocked"
        ok, rec, _s = dep.add_withdrawal_request("jane", 80)
        assert ok and rec["investor"] == "Jane" and rec["status"] == "pending"
        # pending request reserves the unlocked capital
        assert dep.add_withdrawal_request("Jane", 30)[2] == "amount_exceeds_unlocked"

    def test_owner_request_bypasses_lock_and_rejects_unknown(self, paths):
        dep = paths
        self._deposit(dep, "Jane", 200, 1)
        assert dep.add_withdrawal_request("Jane", 50, enforce_lock=False)[0] is True
        assert dep.add_withdrawal_request("Nobody", 50, enforce_lock=False)[2] == "invalid_investor"

    def test_approved_request_matched_by_balance_drop_redeems_units(self, paths, monkeypatch, tmp_path):
        dep = paths
        state_path = str(tmp_path / "bal.json")
        orig_load, orig_save = dep._load_balance_state, dep._save_balance_state
        monkeypatch.setattr(dep, "_load_balance_state", lambda: orig_load(state_path))
        monkeypatch.setattr(dep, "_save_balance_state", lambda b, a: orig_save(b, a, state_path))
        monkeypatch.setattr(dep, "_last_balance_check_monotonic", 0.0)
        self._deposit(dep, "Jane", 300, 40)
        _ok, rec, _s = dep.add_withdrawal_request("Jane", 120)
        # not approved yet: a drop is just an unassigned withdrawal, not a match
        assert dep.find_matching_approved_withdrawal(120.0) is None
        assert dep.approve_withdrawal_request(rec["id"])[0]
        dep._save_balance_state(1000.0, dep._now_iso())
        result = dep.detect_and_record_balance_change(880.0, [], min_interval_seconds=0)
        assert result["action"] == "withdrawal_matched"
        done = dep.load_withdrawal_requests()[0]
        assert done["status"] == "completed" and done["completed_via"] == "broker"
        w = [e for e in dep.load_entries(dep.LOG_PATH) if e["type"] == "withdrawal"]
        assert len(w) == 1 and w[0]["investor"] == "Jane" and w[0]["amount"] == 120.0

    def test_unmatched_approved_request_transfers_units_to_owner(self, paths):
        dep = paths
        self._deposit(dep, "Aleks", 100, 60)
        self._deposit(dep, "Jane", 300, 40)
        _ok, rec, _s = dep.add_withdrawal_request("Jane", 120)
        dep.approve_withdrawal_request(rec["id"])
        # too recent for the automatic fallback
        assert dep.settle_stale_approved_withdrawals() == []
        later = dep._now_naive_uk() + __import__("datetime").timedelta(days=4)
        assert dep.settle_stale_approved_withdrawals(now=later) == [rec["id"]]
        entries = dep.load_entries(dep.LOG_PATH)
        assert any(e["type"] == "withdrawal" and e["investor"] == "Jane" and e["amount"] == 120.0 for e in entries)
        assert any(e["type"] == "deposit" and e["investor"] == "Aleks" and e["amount"] == 120.0 for e in entries)
        bd = {r["investor"]: r for r in dep.investor_breakdown(entries, [], current_balance=400.0)["investors"]}
        assert bd["Jane"]["current_value"] == pytest.approx(180.0)
        assert bd["Aleks"]["current_value"] == pytest.approx(220.0)
        assert dep.load_withdrawal_requests()[0]["completed_via"] == "owner"

    def test_manual_settle_requires_approval(self, paths):
        dep = paths
        self._deposit(dep, "Jane", 300, 40)
        _ok, rec, _s = dep.add_withdrawal_request("Jane", 100)
        assert dep.complete_withdrawal_request(rec["id"], "owner")[2] == "invalid_transition"
        dep.approve_withdrawal_request(rec["id"])
        assert dep.complete_withdrawal_request(rec["id"], "owner")[0] is True
        assert dep.complete_withdrawal_request(rec["id"], "owner")[2] == "invalid_transition"


class TestOvernightFeeAndInvestorRoi:
    @pytest.fixture
    def dep(self, tmp_path, monkeypatch):
        import deposits
        monkeypatch.setattr(deposits, "LOG_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setattr(deposits, "BALANCE_STATE_PATH", str(tmp_path / "bal.json"))
        monkeypatch.setattr(deposits, "OVERNIGHT_FEE_STATE_PATH", str(tmp_path / "fee.json"))
        return deposits

    def _at(self, hh, mm, day=1):
        import datetime
        return datetime.datetime(2026, 1, day, hh, mm, 10)

    def test_overnight_drop_is_recorded_as_editable_broker_fee(self, dep):
        assert dep.check_overnight_fee(1000.0, now=self._at(21, 0))["action"] == "baseline_set"
        assert dep.check_overnight_fee(999.1, now=self._at(21, 3))["action"] == "waiting"
        res = dep.check_overnight_fee(998.4, now=self._at(21, 5))
        assert res["action"] == "fee_recorded" and res["amount"] == pytest.approx(1.6)
        assert dep.check_overnight_fee(997.0, now=self._at(21, 6))["action"] == "waiting"
        fees = [e for e in dep.load_entries(dep.LOG_PATH) if e["type"] == "fee"]
        assert len(fees) == 1 and fees[0]["investor"] == "Broker"

        assert dep.update_fee_amount(fees[0]["id"], 2.25, path=dep.LOG_PATH) == (True, "updated")
        assert dep.load_entries(dep.LOG_PATH)[0]["amount"] == 2.25

    def test_overnight_check_ignores_trade_pnl_and_outside_window(self, dep):
        import datetime
        assert dep.check_overnight_fee(1000.0, now=self._at(20, 59))["reason"] == "outside_window"
        dep.check_overnight_fee(1000.0, now=self._at(21, 0))
        pnl = [{"ts": self._at(21, 2), "pnl": 10.0}]
        assert dep.check_overnight_fee(1010.0, pnl, now=self._at(21, 5))["action"] == "no_fee"
        assert not [e for e in dep.load_entries(dep.LOG_PATH) if e["type"] == "fee"]

    def test_update_fee_amount_only_edits_fees(self, dep):
        _ok, rec, _s = dep.add_entry("deposit", 100, "Jane", path=dep.LOG_PATH)
        assert dep.update_fee_amount(rec["id"], 5, path=dep.LOG_PATH)[1] == "not_a_fee"

    def test_investor_roi_page_shows_only_own_figures(self, monkeypatch, tmp_path):
        from flask import Flask
        import dashboard as dm

        entries = [
            {"id": "1", "type": "deposit", "amount": 100.0, "investor": "Jane", "occurred_at": "2026-01-01"},
            {"id": "2", "type": "deposit", "amount": 900.0, "investor": "Aleks", "occurred_at": "2026-01-01"},
        ]
        monkeypatch.setattr(dm, "_build_request_context", lambda: {
            "account": {"balance": 1100}, "combined_trades": [],
        })
        monkeypatch.setattr(dm.deposits, "list_entries_sorted", lambda: entries)
        monkeypatch.setattr(dm.deposits, "load_tiers", lambda *a, **k: {})
        monkeypatch.setattr(dm, "_read_identity", lambda: {"role": "investor", "name": "jane", "logged_in": True})

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dm.dashboard)
        body = app.test_client().get("/dashboard/roi").get_data(as_text=True)

        assert "My ROI Overview" in body and "My Investor Breakdown" in body
        assert "Jane" in body and "Aleks" not in body
        assert "Ownership Share" not in body and "Per-Investor Breakdown" not in body

    def test_title_links_by_role(self, monkeypatch):
        from flask import Flask
        import dashboard as dm

        app = Flask(__name__, template_folder="templates")
        app.register_blueprint(dm.dashboard)
        client = app.test_client()
        client.set_cookie("dashboard_auth", "1")
        client.set_cookie("dashboard_role", "viewer")
        monkeypatch.setattr(dm, "_build_request_context", lambda: {
            "account": {}, "positions": [], "combined_trades": [],
            "analytics": dm._safe_analytics({}), "weekly_analytics": dm._safe_analytics({}),
            "monthly_analytics": dm._safe_analytics({}),
        })
        assert 'href="/" class="title-link"' in client.get("/dashboard").get_data(as_text=True)
        client.set_cookie("dashboard_role", "investor")
        assert 'href="/dashboard" class="title-link"' in client.get("/dashboard").get_data(as_text=True)
