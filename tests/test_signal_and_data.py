"""Future data cannot enter a signal; malformed candle data fails closed. SYNTHETIC fixtures."""
import pytest
from conftest import T0

from deskcore.models import PASS, REJECT, UNKNOWN, DataError
from deskcore.sources import closed_only, parse_klines
from deskcore.synthetic import hourly_breakout, make_candle
from deskcore.timeutil import HOUR_MS
from roles import signal


def test_breakout_signal_passes(settings):
    cs = hourly_breakout(T0)
    as_of = cs[-1].close_time + 1
    r = signal.evaluate(cs, as_of, settings)
    assert r.verdict == PASS
    assert r.data["stop"] < r.data["reference_close"] < r.data["target"]
    risk = r.data["reference_close"] - r.data["stop"]
    assert r.data["target"] == pytest.approx(r.data["reference_close"] + 2 * risk, rel=1e-6)


def test_future_candle_cannot_enter_signal(settings):
    cs = hourly_breakout(T0)[:-1]  # no breakout among these closed candles
    as_of = cs[-1].close_time + 1
    base = signal.evaluate(cs, as_of, settings)
    assert base.verdict == REJECT
    # A huge candle that closes AFTER the decision time must be ignored.
    future = make_candle(cs[-1].open_time + HOUR_MS, HOUR_MS, cs[-1].close, cs[-1].close + 50)
    r = signal.evaluate(cs + [future], as_of, settings)
    assert r.verdict == REJECT
    assert r.data["reference_close"] == base.data["reference_close"]
    assert any("excluded 1" in x for x in r.reasons)


def test_in_progress_candle_is_not_closed():
    cs = hourly_breakout(T0)
    now = cs[-1].open_time + 10 * 60_000  # mid-candle
    assert closed_only(cs, now)[-1] == cs[-2]


def test_insufficient_history_is_unknown(settings):
    cs = hourly_breakout(T0, n=30)
    assert signal.evaluate(cs, cs[-1].close_time + 1, settings).verdict == UNKNOWN


def _raw(c):
    return [c.open_time, str(c.open), str(c.high), str(c.low), str(c.close), str(c.volume),
            c.close_time, str(c.quote_volume), 0, "0", "0", "0"]


def test_parse_klines_rejects_duplicates_and_disorder():
    cs = hourly_breakout(T0, n=3)
    rows = [_raw(c) for c in cs]
    assert len(parse_klines(rows, "1h")) == 3
    with pytest.raises(DataError):
        parse_klines([rows[0], rows[0]], "1h")
    with pytest.raises(DataError):
        parse_klines([rows[1], rows[0]], "1h")


def test_parse_klines_rejects_bad_rows():
    c = hourly_breakout(T0, n=1)[0]
    bad_ts = _raw(c)
    bad_ts[6] = c.close_time + 5
    for rows in ([["x"]], [bad_ts], "not-a-list"):
        with pytest.raises(DataError):
            parse_klines(rows, "1h")
