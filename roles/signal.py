"""Role 5 - Signal: sample LONG-only breakout rule on CLOSED 1h candles.

Rule: last closed close > max(high of prior 20 closed candles), SMA(50) rising,
ATR(14) from closed bars; stop = close - 1.5*ATR; target = close + 2*(close - stop).
"""
from __future__ import annotations

from statistics import fmean

from deskcore.models import PASS, REJECT, UNKNOWN, Candle, RoleResult
from deskcore.timeutil import iso

ROLE = "5-Signal"


def atr(candles: list[Candle], period: int) -> float:
    trs = []
    for i in range(len(candles) - period, len(candles)):
        c, pc = candles[i], candles[i - 1].close
        trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return fmean(trs)


def evaluate(candles: list[Candle], as_of_ms: int, s: dict) -> RoleResult:
    usable = [c for c in candles if c.close_time < as_of_ms]
    reasons: list[str] = []
    if len(usable) != len(candles):
        reasons.append(f"excluded {len(candles) - len(usable)} candle(s) not closed before decision time")
    lb, sp, ap = s["breakout_lookback"], s["sma_period"], s["atr_period"]
    need = max(lb, sp, ap) + 1
    if len(usable) < need:
        return RoleResult(ROLE, UNKNOWN, reasons + [f"need {need} closed candles, have {len(usable)}"])

    last = usable[-1]
    prior_high = max(c.high for c in usable[-(lb + 1):-1])
    sma_now = fmean(c.close for c in usable[-sp:])
    sma_prev = fmean(c.close for c in usable[-sp - 1:-1])
    a = atr(usable, ap)
    stop = last.close - s["atr_stop_mult"] * a
    target = last.close + s["reward_risk"] * (last.close - stop)
    breakout = last.close > prior_high
    rising = sma_now > sma_prev
    data = {"signal_candle_close_utc": iso(last.close_time + 1),
            "signal_candle_close_ms": last.close_time + 1,
            "reference_close": last.close, "prior_high": prior_high,
            "sma_now": round(sma_now, 8), "sma_prev": round(sma_prev, 8),
            "atr": round(a, 8), "stop": round(stop, 8), "target": round(target, 8),
            "breakout": breakout, "sma_rising": rising}
    reasons.append(f"close {last.close:g} vs prior {lb}-bar high {prior_high:g} "
                   f"({'breakout' if breakout else 'no breakout'}); "
                   f"SMA{sp} {'rising' if rising else 'not rising'}; ATR{ap} {a:.6g}")
    if breakout and rising and a > 0 and stop > 0:
        reasons.append(f"LONG watch: stop {stop:.6g}, target {target:.6g}")
        return RoleResult(ROLE, PASS, reasons, data)
    return RoleResult(ROLE, REJECT, reasons + ["no setup"], data)
