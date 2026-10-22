import pytest

import trail_sl


@pytest.fixture(autouse=True)
def _reset_trail_state():
    trail_sl._sent_state.clear()
    yield
    trail_sl._sent_state.clear()


def _mock_position(**overrides):
    base = {
        "dealId": "D1",
        "direction": "Long",
        "price": 100.0,
        "current_price": 101.0,
        "stopLevel": 100.1,
    }
    base.update(overrides)
    return base


def test_trailing_sl_updates_long_position(monkeypatch):
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [_mock_position()])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.005)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.30)
    monkeypatch.setattr(trail_sl.config, "TRAIL_TIGHTEN_STEP_PERC", 0.15)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][0] == "D1"
    assert calls[0][1] == 100.45


def test_trailing_sl_supports_whole_percent_inputs(monkeypatch):
    pos = _mock_position(direction="Short", current_price=98.0, stopLevel=101.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 1)   # 1%
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 30)          # 30%
    monkeypatch.setattr(trail_sl.config, "TRAIL_TIGHTEN_STEP_PERC", 0.15)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][1] == 99.1


def test_trailing_sl_tightens_further_as_profit_grows(monkeypatch):
    pos = _mock_position(current_price=104.0, stopLevel=101.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.005)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.60)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][1] == 103.8


def test_trailing_sl_caps_at_trail_max_perc_deep_in_profit(monkeypatch):
    # Far beyond activation, the trail percent should hit the TRAIL_MAX_PERC
    # ceiling (95% by default) rather than keep climbing unbounded.
    pos = _mock_position(current_price=150.0, stopLevel=101.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.005)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.65)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    # profit = 50.0, capped trail percent = 0.95 -> entry + 50 * 0.95
    assert calls[0][1] == 147.5


def test_trailing_sl_skips_unknown_direction(monkeypatch):
    pos = _mock_position(direction="SIDEWAYS", stopLevel=None)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert calls == []


def test_trailing_sl_activates_at_half_of_tp_move(monkeypatch):
    pos = _mock_position(current_price=104.9, stopLevel=100.1, profitLevel=110.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0.5)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.50)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()
    assert calls == []

    pos["current_price"] = 105.0
    trail_sl.run_trailing_sl()
    assert len(calls) == 1


def test_trailing_sl_reads_enriched_limit_level_as_tp(monkeypatch):
    # Enriched positions expose the TP as "limitLevel"; BE-style trade:
    # entry 228.81, TP 233.11 -> 50% of TP move (~0.94%) beats the 1% default.
    pos = _mock_position(price=228.81, current_price=231.0, stopLevel=221.0, limitLevel=233.11)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.01)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0.5)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PNL_GBP", 15.0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.50)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()
    assert len(calls) == 1 and calls[0][1] > 221.0


def test_trailing_sl_activates_via_pnl_gbp_floor_before_perc_threshold(monkeypatch):
    # A high percentage activation threshold (10%) would not normally be hit
    # by a 1% price move, but a flat GBP PnL floor should activate trailing
    # anyway once that floor is reached, regardless of the percentage move.
    pos = _mock_position(current_price=101.0, stopLevel=None, profit=5.0, currency="GBP")
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.10)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PNL_GBP", 2.0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.70)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    # profit = 1.0, trail_sl = entry + 1.0 * 0.70
    assert calls[0][1] == 100.7


def test_trailing_sl_pnl_floor_does_not_activate_below_threshold(monkeypatch):
    pos = _mock_position(current_price=101.0, stopLevel=None, profit=1.0, currency="GBP")
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.10)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PNL_GBP", 2.0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.70)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert calls == []


def _setup_throttle(monkeypatch, positions):
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: positions)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.005)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.30)
    monkeypatch.setattr(trail_sl.config, "TRAIL_TIGHTEN_STEP_PERC", 0.15)
    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda d, sl: calls.append(sl) or True)
    return calls


def test_trailing_sl_does_not_resend_same_sl_with_stale_broker_value(monkeypatch):
    calls = _setup_throttle(monkeypatch, [_mock_position()])
    trail_sl.run_trailing_sl()
    trail_sl.run_trailing_sl()  # broker still reports the old stop
    assert len(calls) == 1


def test_trailing_sl_ignores_tiny_moves_after_first_lock(monkeypatch):
    pos = _mock_position(stopLevel=100.45, current_price=101.0)
    calls = _setup_throttle(monkeypatch, [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_TIGHTEN_STEP_PERC", 0.0)
    pos["current_price"] = 101.1  # would nudge SL by ~0.03 (< 0.1% of entry)
    trail_sl.run_trailing_sl()
    assert calls == []


def test_trailing_sl_failure_cooldown(monkeypatch):
    calls = _setup_throttle(monkeypatch, [_mock_position()])
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda d, sl: calls.append(sl) or False)
    trail_sl.run_trailing_sl()
    trail_sl.run_trailing_sl()
    assert len(calls) == 1
