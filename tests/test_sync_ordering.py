import json

import history_sync
import session
import trade_log
from trade_log import _humanize, time_sort_ts


def test_humanize_converts_aware_times_to_uk():
    assert _humanize("2026-10-22T14:00:00Z") == "22-10-2026 15:00:00"  # BST
    assert _humanize("2026-10-22T15:00:00+01:00") == "22-10-2026 15:00:00"
    assert _humanize("2026-12-22T15:00:00+00:00") == "22-12-2026 15:00:00"  # GMT
    assert _humanize("2026-10-22 15:00:00") == "22-10-2026 15:00:00"  # naive = UK


def test_time_sort_ts_orders_mixed_formats_chronologically():
    values = [
        "2026-10-22T14:30:00+01:00",   # 13:30 UTC
        "22-10-2026 15:00:00",         # 14:00 UTC (naive UK)
        "2026-10-22T13:45:00Z",        # 13:45 UTC
    ]
    ordered = sorted(values, key=time_sort_ts)
    assert ordered == [values[0], values[2], values[1]]
    assert time_sort_ts("garbage") is None


def test_broker_timestamp_is_uk_local():
    assert history_sync._parse_broker_timestamp("2026-10-22T14:00:00") == "2026-10-22T15:00:00+01:00"
    assert history_sync._parse_broker_timestamp(1792677600) is not None


def test_get_positions_returns_none_on_failure_and_caches(monkeypatch):
    session._positions_cache.update({"ts": 0.0, "data": None})
    session._positions_state.update({"failures": 0, "backoff_until": 0.0})

    class Resp:
        status_code = 200

        def json(self):
            return {"positions": [{"position": {"dealId": "A"}}]}

    calls = []
    monkeypatch.setattr(session, "request", lambda *a, **k: calls.append(1) or Resp())
    assert session.get_positions() == [{"position": {"dealId": "A"}}]
    assert session.get_positions() == [{"position": {"dealId": "A"}}]
    assert len(calls) == 1  # second call served from cache

    monkeypatch.setattr(session, "request", lambda *a, **k: None)
    assert session.get_positions(fresh=True) is None
    assert session.last_known_positions() is not None
    # Back-off: no further broker call once the cache has expired
    session._positions_cache["ts"] = 0.0
    assert session.get_positions() is None

    session._positions_cache.update({"ts": 0.0, "data": None})
    session._positions_state.update({"failures": 0, "backoff_until": 0.0})


def test_exit_price_edit_closes_open_trade_missing_from_broker(tmp_path, monkeypatch):
    path = str(tmp_path / "log.json")
    with open(path, "w") as f:
        json.dump([{"dealId": "D1", "ticker": "LRCX", "side": "short", "size": 2,
                    "entry_price": 261.21, "status": "OPEN"}], f)

    monkeypatch.setattr(trade_log, "is_trade_delete_candidate", lambda t, *a, **k: True)

    monkeypatch.setattr(trade_log, "_confirm_trade_missing_from_broker", lambda t: False)
    ok, _, status = trade_log.update_trade_exit_price_entry(0, 235.07, path=path)
    assert (ok, status) == (False, "broker_still_open")

    monkeypatch.setattr(trade_log, "_confirm_trade_missing_from_broker", lambda t: True)
    ok, updated, status = trade_log.update_trade_exit_price_entry(0, 235.07, path=path)
    assert ok and status == "updated"
    assert updated["status"] == "CLOSED" and updated["time_exited"]
    assert updated["exit_price"] == 235.07
