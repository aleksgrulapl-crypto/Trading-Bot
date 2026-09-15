import trail_sl


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
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.30)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][0] == "D1"
    assert calls[0][1] == 100.4


def test_trailing_sl_supports_whole_percent_inputs(monkeypatch):
    pos = _mock_position(direction="Short", current_price=98.0, stopLevel=101.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 1)   # 1%
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 30)          # 30%

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][1] == 99.2


def test_trailing_sl_tightens_further_as_profit_grows(monkeypatch):
    pos = _mock_position(current_price=104.0, stopLevel=101.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.005)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.60)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert len(calls) == 1
    assert calls[0][1] == 103.6


def test_trailing_sl_skips_unknown_direction(monkeypatch):
    pos = _mock_position(direction="SIDEWAYS", stopLevel=None)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()

    assert calls == []


def test_trailing_sl_activates_at_quarter_of_tp_move(monkeypatch):
    pos = _mock_position(current_price=102.4, stopLevel=100.1, profitLevel=110.0)
    monkeypatch.setattr(trail_sl.session, "get_positions", lambda: [{"position": {}, "market": {}}])
    monkeypatch.setattr(trail_sl.session, "enrich_positions", lambda _: [pos])
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_PERC", 0.0)
    monkeypatch.setattr(trail_sl.config, "TRAIL_ACTIVATION_TP_FRACTION", 0.25)
    monkeypatch.setattr(trail_sl.config, "TRAIL_SL_PERC", 0.50)

    calls = []
    monkeypatch.setattr(trail_sl, "_update_stop_level", lambda deal_id, sl: calls.append((deal_id, sl)) or True)

    trail_sl.run_trailing_sl()
    assert calls == []

    pos["current_price"] = 102.5
    trail_sl.run_trailing_sl()
    assert len(calls) == 1
    assert calls[0][1] == 101.25
