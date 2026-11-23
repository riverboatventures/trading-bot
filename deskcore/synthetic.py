"""Deterministic SYNTHETIC market world for unit tests and the smoke test.

Nothing here is market data. Every output produced from it is labelled SYNTHETIC.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from .models import Candle, KlineResult, Observation
from .timeutil import DAY_MS, HOUR_MS, INTERVAL_MS, MIN_MS, iso

SYN_URL = "synthetic://fixture"


def make_candle(open_time: int, step: int, o: float, c: float, hi_pad: float = 0.2,
                lo_pad: float = 0.2, qv: float = 1_000_000.0) -> Candle:
    return Candle(open_time=open_time, open=o, high=max(o, c) + hi_pad, low=min(o, c) - lo_pad,
                  close=c, volume=qv / c, close_time=open_time + step - 1, quote_volume=qv)


def hourly_breakout(end_open_ms: int, n: int = 130, base: float = 100.0) -> list[Candle]:
    """Gentle uptrend with wiggles; the final candle breaks above the prior 20 highs."""
    out, prev = [], base
    for i in range(n):
        t = end_open_ms - (n - 1 - i) * HOUR_MS
        if i == n - 1:
            c = prev + 3.0
        else:
            c = base + 0.05 * i + 0.6 * math.sin(i / 3.0)
        out.append(make_candle(t, HOUR_MS, prev, c))
        prev = c
    return out


def minute_path(start_open_ms: int, start_price: float, slope: float, n: int) -> list[Candle]:
    out, prev = [], start_price
    for i in range(n):
        c = start_price + slope * (i + 1)
        out.append(make_candle(start_open_ms + i * MIN_MS, MIN_MS, prev, c, 0.01, 0.01, 50_000.0))
        prev = c
    return out


class SyntheticSource:
    """Same interface as LiveSource, backed by in-memory fixtures.

    Like a real exchange, it never returns a candle that has not opened yet
    relative to the injected clock.
    """

    name = "SYNTHETIC"

    def __init__(self, now_fn, candles: dict, protocols: dict | None = None,
                 fees: dict | None = None, statuses: dict | None = None):
        self.now_fn = now_fn
        self.candles = candles            # {symbol: {"1h": [...], "1m": [...]}}
        self.protocols = protocols or {}  # {slug: payload}
        self.fee_data = fees or {}        # {(slug, dataType): payload}
        self.statuses = statuses or {}
        self.calls: list[str] = []
        self.fail = False

    def _check(self, what: str):
        self.calls.append(what)
        if self.fail:
            from .models import DataError
            raise DataError(f"SYNTHETIC network failure on {what}")

    def symbol_status(self, symbol):
        self._check(f"status:{symbol}")
        return Observation(SYN_URL, self.now_fn(),
                           {"symbol": symbol, "status": self.statuses.get(symbol, "TRADING"),
                            "quoteAsset": "USDT"})

    def klines_range(self, symbol, interval, start_ms, end_ms):
        self._check(f"klines:{symbol}:{interval}")
        now = self.now_fn()
        step = INTERVAL_MS[interval]
        start = start_ms - start_ms % step
        cs = [c for c in self.candles.get(symbol, {}).get(interval, [])
              if start <= c.open_time <= end_ms and c.open_time <= now]
        return KlineResult(cs, [SYN_URL], now)

    def protocol(self, slug):
        self._check(f"protocol:{slug}")
        p = self.protocols.get(slug)
        return Observation(SYN_URL, self.now_fn(), p) if p is not None else None

    def fees(self, slug, data_type):
        self._check(f"fees:{slug}:{data_type}")
        p = self.fee_data.get((slug, data_type))
        return Observation(SYN_URL, self.now_fn(), p) if p is not None else None


class Clock:
    def __init__(self, ms: int):
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def build_world(root: Path, start_ms: int):
    """Two synthetic assets: SYNA (passes every role) and SYNB (blocked by an
    unlock 3 days away). Returns (clock, source, info)."""
    signal_open = start_ms - HOUR_MS          # signal candle closes exactly at start_ms
    scan_ms = start_ms + 5 * MIN_MS
    clock = Clock(scan_ms)
    hourly = hourly_breakout(signal_open)
    last = hourly[-1]
    # 1m path from the start of the experiment: rises steadily -> hits target.
    minutes = minute_path(start_ms, last.close, 0.03, 6 * 60)
    candles = {"SYNAUSDT": {"1h": hourly, "1m": minutes},
               "SYNBUSDT": {"1h": hourly, "1m": minutes}}
    day_s = (scan_ms // 1000) - 3600
    protocols = {
        "syn-a": {"name": "Synthetic A", "symbol": "SYNA", "mcap": 5e8,
                  "tvl": [{"date": day_s - 86400, "totalLiquidityUSD": 9e8},
                          {"date": day_s, "totalLiquidityUSD": 1e9}]},
        "syn-b": {"name": "Synthetic B", "symbol": "SYNB", "mcap": None,
                  "tvl": [{"date": day_s, "totalLiquidityUSD": 2e8}]},
    }
    fees = {}
    for slug in ("syn-a", "syn-b"):
        fees[(slug, "dailyFees")] = {"total24h": 1e5, "total30d": 3e6, "totalDataChart": [[day_s, 1e5]]}
        fees[(slug, "dailyRevenue")] = {"total24h": 2e4, "total30d": 6e5, "totalDataChart": [[day_s, 2e4]]}
    source = SyntheticSource(clock, candles, protocols, fees)

    cfg = Path(root) / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "asset_map.csv").write_text(
        "ticker,binance_symbol,defillama_slug,verification_url,verified_utc,notes\n"
        f"SYNA,SYNAUSDT,syn-a,synthetic://verify-a,{iso(start_ms)[:10]},SYNTHETIC\n"
        f"SYNB,SYNBUSDT,syn-b,synthetic://verify-b,{iso(start_ms)[:10]},SYNTHETIC\n")
    (cfg / "unlocks.csv").write_text(
        "ticker,status,next_unlock_utc,unlock_pct_supply,source_url,checked_utc,notes\n"
        f"SYNA,none_scheduled,,,https://example.invalid/syna-tokenomics,{iso(start_ms - DAY_MS)},SYNTHETIC\n"
        f"SYNB,scheduled,{iso(start_ms + 3 * DAY_MS)},2.5,https://example.invalid/synb-unlocks,"
        f"{iso(start_ms - DAY_MS)},SYNTHETIC\n")
    return clock, source, {"signal_candle": last, "scan_ms": scan_ms}


def run_smoke(root: Path, out=print) -> dict:
    """End-to-end deterministic run on SYNTHETIC data in an isolated folder."""
    from .pipeline import Context, paper_step, report, scan

    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "README.txt").write_text("SYNTHETIC smoke-test output. Not market data. Not a result.\n")
    start = 1_767_225_600_000  # 2026-01-01T00:00:00Z, fixed for determinism
    clock, source, _ = build_world(root, start)
    ctx = Context(root=root, source=source, now_fn=clock, label="SYNTHETIC", out=out)
    out("#" * 70 + "\n# SYNTHETIC SMOKE TEST - fixture data only, results are NOT market results\n" + "#" * 70)
    summary = scan(ctx)
    clock.ms += 2 * MIN_MS
    step1 = paper_step(ctx)
    clock.ms += 3 * HOUR_MS
    step2 = paper_step(ctx)
    rep = report(ctx)
    return {"scan": summary, "step1": step1, "step2": step2, "report": rep, "root": str(root)}
