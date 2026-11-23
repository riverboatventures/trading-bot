"""Risk includes costs, position cap, daily stop, fills and exits. SYNTHETIC fixtures."""
import pytest
from conftest import T0

from deskcore.models import PASS, REJECT
from deskcore.synthetic import make_candle, minute_path
from deskcore.timeutil import MIN_MS
from roles import executor, risk_sizing


def acct(**kw):
    a = {"experiment_active": True, "cash": 250.0, "equity": 250.0, "open_positions": 0,
         "pending_orders": 0, "day_realized_pnl": 0.0, "day_start_equity": 250.0}
    a.update(kw)
    return a


SIG = {"reference_close": 10.0, "stop": 9.5, "target": 11.0}


def test_position_risk_includes_costs(settings):
    sz = risk_sizing.size_position(10.0, 9.5, 250.0, 250.0, settings)
    # Guide example: $2.50 risk / $0.50 raw distance = 5 units BEFORE costs.
    assert sz["qty"] < 5.0
    assert sz["per_unit_loss_incl_costs"] > 0.5
    assert sz["max_loss_usd"] <= 2.5 + 1e-9
    # Recompute the actual stop-out loss from first principles.
    fee, slip = 0.001, 0.0005
    q = sz["qty"]
    cash_out = q * 10 * (1 + slip) * (1 + fee)
    cash_in = q * 9.5 * (1 - slip) * (1 - fee)
    assert cash_out - cash_in <= 2.5 + 1e-9


def test_zero_costs_matches_guide_example(settings):
    s = {**settings, "fee_pct_per_side": 0.0, "slippage_pct_per_side": 0.0}
    assert risk_sizing.size_position(10.0, 9.5, 250.0, 250.0, s)["qty"] == pytest.approx(5.0)


def test_no_leverage_cash_cap(settings):
    sz = risk_sizing.size_position(10.0, 9.99, 250.0, 250.0, settings)  # tiny stop -> huge qty
    assert sz["capped_by"] == "cash"
    assert sz["cash_required_usd"] <= 250.0 + 1e-9


def test_one_position_cap(settings):
    assert risk_sizing.evaluate(SIG, acct(open_positions=1), settings).verdict == REJECT
    assert risk_sizing.evaluate(SIG, acct(pending_orders=1), settings).verdict == REJECT
    assert risk_sizing.evaluate(SIG, acct(), settings).verdict == PASS


def test_daily_stop(settings):
    assert risk_sizing.evaluate(SIG, acct(day_realized_pnl=-7.5), settings).verdict == REJECT
    assert risk_sizing.evaluate(SIG, acct(day_realized_pnl=-7.4), settings).verdict == PASS


def test_experiment_window_closed_blocks(settings):
    assert risk_sizing.evaluate(SIG, acct(experiment_active=False), settings).verdict == REJECT


def pending(**kw):
    p = {"trade_id": 1, "symbol": "SYNAUSDT", "created_ms": T0, "signal_close_ms": T0 - 5 * MIN_MS,
         "ref_price": 10.0, "stop": 9.5, "target": 11.0}
    p.update(kw)
    return p


def test_fill_uses_later_observed_price_not_signal_close(settings):
    before = make_candle(T0 - MIN_MS, MIN_MS, 9.0, 9.0, 0, 0)  # before the proposal: must be ignored
    later = minute_path(T0, 10.2, 0.0, 3)
    res = executor.try_fill(pending(), [before] + later, T0 + 3 * MIN_MS, acct(), settings, T0 + 10**9)
    assert res["status"] == "FILLED"
    assert res["observed_price_ms"] >= T0
    assert res["price_observed"] == later[0].open != 10.0
    assert res["entry_px"] > res["price_observed"]  # slippage applied
    assert res["entry_fee"] > 0                     # no zero-cost fills


def test_unfilled_without_quote_in_window(settings):
    res = executor.try_fill(pending(), [], T0 + 61 * MIN_MS, acct(), settings, T0 + 10**9)
    assert res["status"] == "UNFILLED"
    res = executor.try_fill(pending(), [], T0 + 30 * MIN_MS, acct(), settings, T0 + 10**9)
    assert res["status"] == "PENDING"


def test_unfilled_if_price_gapped_below_stop(settings):
    cs = minute_path(T0, 9.4, 0.0, 2)
    assert executor.try_fill(pending(), cs, T0 + 5 * MIN_MS, acct(), settings, T0 + 10**9)["status"] == "UNFILLED"


def position(**kw):
    p = {"trade_id": 1, "symbol": "SYNAUSDT", "stop": 9.5, "target": 11.0, "qty": 4.0,
         "cash_out": 40.06, "next_check_ms": T0}
    p.update(kw)
    return p


def test_both_touched_takes_stop_first(settings):
    c = make_candle(T0, MIN_MS, 10.0, 10.0, hi_pad=1.5, lo_pad=0.6)  # high 11.5, low 9.4
    res = executor.advance(position(), [c], T0 + 10 * MIN_MS, settings)
    assert res["event"] == "CLOSED_STOP"
    assert "stop-first" in res["reason"]
    assert res["pnl_net"] < 0


def test_gap_below_stop_exits_at_worse_open(settings):
    c = make_candle(T0, MIN_MS, 9.0, 9.0, 0, 0)
    res = executor.advance(position(), [c], T0 + 10 * MIN_MS, settings)
    assert res["price_observed"] == 9.0


def test_unknown_exit_remains_open(settings):
    cs = minute_path(T0, 10.0, 0.001, 5)
    res = executor.advance(position(), cs, T0 + 10 * MIN_MS, settings)
    assert res["status"] == "OPEN"


def test_unclosed_candle_cannot_trigger_exit(settings):
    c = make_candle(T0, MIN_MS, 10.0, 12.0)  # would hit target, but still in progress
    res = executor.advance(position(), [c], T0 + 30_000, settings)
    assert res["status"] == "OPEN"
