"""End-to-end behaviour on SYNTHETIC data."""
import pytest
from conftest import T0

from deskcore.models import DataError
from deskcore.pipeline import Context, paper_step, report, scan
from deskcore.storage import Store
from deskcore.synthetic import build_world, run_smoke
from deskcore.timeutil import DAY_MS, HOUR_MS, MIN_MS, iso


def make_ctx(tmp_path):
    clock, source, info = build_world(tmp_path, T0)
    ctx = Context(root=tmp_path, source=source, now_fn=clock, label="SYNTHETIC", out=lambda *_: None)
    return ctx, clock, source


def trades(tmp_path):
    return Store(tmp_path).read("paper_trades.csv")


def test_scan_never_records_a_fill(tmp_path):
    ctx, clock, _ = make_ctx(tmp_path)
    summary = scan(ctx)
    assert [s["final"] for s in summary] == ["PROPOSE", "SKIP"]
    assert all(len(s["results"]) == 7 for s in summary)
    assert [t["event"] for t in trades(tmp_path)] == ["PROPOSED"]
    st = Store(tmp_path).load_state()
    assert st["position"] is None and st["cash"] == 250.0


def test_skipped_setup_never_gets_pnl(tmp_path):
    ctx, clock, _ = make_ctx(tmp_path)
    scan(ctx)
    clock.ms += 2 * MIN_MS
    paper_step(ctx)
    clock.ms += 4 * HOUR_MS
    paper_step(ctx)
    assert not any(t["symbol"] == "SYNBUSDT" for t in trades(tmp_path))
    pnl_rows = [t for t in trades(tmp_path) if t["pnl_net_usd"] not in ("", "0", "0.0")]
    assert all(t["symbol"] == "SYNAUSDT" for t in pnl_rows)


def test_full_cycle_and_hold_uses_identical_dates(tmp_path):
    ctx, clock, _ = make_ctx(tmp_path)
    scan(ctx)
    clock.ms += 2 * MIN_MS
    paper_step(ctx)
    clock.ms += 3 * HOUR_MS
    paper_step(ctx)
    rep = report(ctx)
    ev = [t["event"] for t in trades(tmp_path)]
    assert ev == ["PROPOSED", "FILLED", "CLOSED_TARGET"]
    st = Store(tmp_path).load_state()
    start = st["experiment"]["start_ms"]
    entries = st["hold"]["entries"]
    filled = [t for t in trades(tmp_path) if t["event"] == "FILLED"][0]
    for e in entries.values():
        assert e["price_ms"] >= start
        assert iso(e["price_ms"]) == filled["observed_price_utc"]  # same first observed minute
    marks = {m[1] for m in rep["marks"].values()}
    assert len(marks) == 1 and list(marks)[0] <= rep["as_of"]
    assert rep["closed"] == 1 and rep["realized"] > 0
    assert rep["hold_value"] is not None


def test_one_position_cap_across_scans(tmp_path):
    ctx, clock, _ = make_ctx(tmp_path)
    scan(ctx)
    summary = scan(ctx)  # second scan while proposal pending
    assert all(s["final"] == "SKIP" for s in summary)
    assert [t["event"] for t in trades(tmp_path)].count("PROPOSED") == 1


def test_data_failure_stops_and_preserves_records(tmp_path):
    ctx, clock, source = make_ctx(tmp_path)
    scan(ctx)
    before_state = (tmp_path / "state" / "desk_state.json").read_text()
    before_trades = (tmp_path / "output" / "paper_trades.csv").read_text()
    source.fail = True
    with pytest.raises(DataError):
        scan(ctx)
    with pytest.raises(DataError):
        paper_step(ctx)
    assert (tmp_path / "state" / "desk_state.json").read_text() == before_state
    assert (tmp_path / "output" / "paper_trades.csv").read_text() == before_trades
    decisions = Store(tmp_path).read("decisions.csv")
    assert decisions[-1]["verdict"] == "DATA_FAIL"


def test_open_position_stays_open_after_window(tmp_path):
    ctx, clock, source = make_ctx(tmp_path)
    # Flatten the 1m path so neither stop nor target is ever touched.
    sym = source.candles["SYNAUSDT"]
    from deskcore.synthetic import minute_path
    sym["1m"] = minute_path(T0, sym["1h"][-1].close, 0.0, 6 * 24 * 60)
    scan(ctx)
    clock.ms += 2 * MIN_MS
    paper_step(ctx)
    clock.ms += 6 * DAY_MS
    paper_step(ctx)
    rep = report(ctx)
    assert rep["status"] == "COMPLETE"
    assert Store(tmp_path).load_state()["position"] is not None
    assert [t["event"] for t in trades(tmp_path)] == ["PROPOSED", "FILLED"]
    assert rep["closed"] == 0


def test_experiment_timer_persists_and_is_not_reset(tmp_path):
    ctx, clock, _ = make_ctx(tmp_path)
    scan(ctx)
    start = Store(tmp_path).load_state()["experiment"]["start_ms"]
    clock.ms += DAY_MS
    scan(ctx)
    exp = Store(tmp_path).load_state()["experiment"]
    assert exp["start_ms"] == start and exp["end_ms"] == start + 5 * DAY_MS


def test_smoke_test_is_labelled_synthetic(tmp_path):
    res = run_smoke(tmp_path / "smoke", out=lambda *_: None)
    out = tmp_path / "smoke" / "output"
    for name in ("decisions.csv", "paper_trades.csv", "daily_equity.csv", "hold.csv"):
        rows = Store(tmp_path / "smoke").read(name)
        assert rows and all(r["run_label"] == "SYNTHETIC" for r in rows)
    assert "SYNTHETIC" in (out / "report_SYNTHETIC_latest.md").read_text()
    assert res["report"]["closed"] == 1
