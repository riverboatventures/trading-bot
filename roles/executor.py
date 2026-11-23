"""Role 7 - Paper Executor/Auditor.

* scan: decides whether a PENDING proposal is saved. Never records a fill.
* paper-step: fills a pending proposal only from an ACTUALLY OBSERVED 1m price
  timestamped after the signal (within the fill window), and advances open
  positions only from later closed 1m candles. If a candle touches both stop
  and target, the stop is assumed to have hit first.
"""
from __future__ import annotations

from deskcore.models import PASS, REJECT, Candle, RoleResult
from deskcore.timeutil import MIN_MS, iso

from .risk_sizing import cost_rates, size_position

ROLE = "7-Paper Executor/Auditor"


def evaluate_scan(prior: list[RoleResult]) -> RoleResult:
    failed = [f"{r.role}={r.verdict}" for r in prior if r.verdict != PASS]
    if failed:
        return RoleResult(ROLE, REJECT, ["SKIP: " + ", ".join(failed), "no proposal saved, no P&L"])
    return RoleResult(ROLE, PASS, ["all six roles PASS -> PENDING paper proposal saved",
                                   "no fill recorded; fill requires a later observed price (paper-step)"])


def liquidation_value(qty: float, price: float, s: dict) -> float:
    fee, slip = cost_rates(s)
    return qty * price * (1 - slip) * (1 - fee)


def try_fill(pending: dict, minute_candles: list[Candle], now_ms: int, account: dict,
             s: dict, end_ms: int) -> dict:
    window_end = pending["created_ms"] + s["fill_window_minutes"] * MIN_MS
    earliest = max(pending["created_ms"], pending["signal_close_ms"] + 1)
    quote = next((c for c in minute_candles
                  if earliest <= c.open_time <= window_end and c.open_time < now_ms
                  and c.open_time < end_ms), None)
    if quote is None:
        if now_ms > window_end or now_ms >= end_ms:
            return {"status": "UNFILLED",
                    "reason": f"no observed quote between {iso(earliest)} and {iso(window_end)}"}
        return {"status": "PENDING", "reason": "waiting for an observed quote after the signal"}

    px = quote.open
    base = {"observed_price_ms": quote.open_time, "price_observed": px}
    if px <= pending["stop"]:
        return {**base, "status": "UNFILLED", "reason": f"observed price {px:g} at/below stop"}
    if px >= pending["target"]:
        return {**base, "status": "UNFILLED", "reason": f"observed price {px:g} at/above target"}
    sz = size_position(px, pending["stop"], account["equity"], account["cash"], s)
    if sz["qty"] <= 0 or sz["notional_usd"] < s["min_notional_usd"]:
        return {**base, "status": "UNFILLED", "reason": "re-sized position below minimum notional"}
    fee, slip = cost_rates(s)
    entry_px = px * (1 + slip)
    qty = sz["qty"]
    return {**base, "status": "FILLED", "qty": qty, "entry_px": entry_px,
            "entry_fee": qty * entry_px * fee, "entry_slippage_usd": qty * (entry_px - px),
            "cash_out": qty * entry_px * (1 + fee), "max_loss_usd": sz["max_loss_usd"],
            "reason": f"filled at observed 1m open {px:g} @ {iso(quote.open_time)} + slippage"}


def advance(position: dict, minute_candles: list[Candle], until_ms: int, s: dict) -> dict:
    """Walk closed 1m candles from position['next_check_ms'] up to until_ms."""
    fee, slip = cost_rates(s)
    stop, target, qty = position["stop"], position["target"], position["qty"]
    last = None
    for c in minute_candles:
        if c.open_time < position["next_check_ms"] or c.close_time >= until_ms:
            continue
        last = c
        hit_stop = c.low <= stop
        hit_target = c.high >= target
        if hit_stop or hit_target:
            if hit_stop:
                raw = min(stop, c.open)  # gap below stop exits at the worse open
                event = "CLOSED_STOP"
                why = "stop hit" + ("; candle touched stop AND target -> stop-first (conservative)"
                                    if hit_target else "")
            else:
                raw = target
                event = "CLOSED_TARGET"
                why = "target hit"
            exit_px = raw * (1 - slip)
            exit_fee = qty * exit_px * fee
            cash_in = qty * exit_px - exit_fee
            return {"status": "CLOSED", "event": event, "exit_ms": c.close_time + 1,
                    "observed_price_ms": c.open_time, "price_observed": raw, "exit_px": exit_px,
                    "exit_fee": exit_fee, "exit_slippage_usd": qty * (raw - exit_px),
                    "cash_in": cash_in, "pnl_net": cash_in - position["cash_out"], "reason": why}
    out = {"status": "OPEN", "reason": "no stop/target touched in observed closed candles"}
    if last is not None:
        out.update(next_check_ms=last.open_time + MIN_MS, mark_price=last.close,
                   mark_ms=last.close_time + 1)
    return out
