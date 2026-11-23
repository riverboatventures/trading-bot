"""Public data sources: Binance SPOT market data and DefiLlama.

Every fetch is recorded with its source URL and UTC retrieval time, and a
trimmed snapshot is written to data/snapshots/ for later audit.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .http import get_json
from .models import Candle, DataError, KlineResult, NotAvailable, Observation
from .timeutil import INTERVAL_MS, iso, utc_date

BINANCE = "https://data-api.binance.vision"
LLAMA = "https://api.llama.fi"
KLINE_LIMIT = 1000


def parse_klines(rows, interval: str) -> list[Candle]:
    """Validate raw Binance kline rows. Any malformed row raises DataError."""
    if interval not in INTERVAL_MS:
        raise DataError(f"unsupported interval {interval}")
    step = INTERVAL_MS[interval]
    if not isinstance(rows, list):
        raise DataError("kline payload is not a list")
    out: list[Candle] = []
    for i, r in enumerate(rows):
        try:
            c = Candle(open_time=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                       close=float(r[4]), volume=float(r[5]), close_time=int(r[6]),
                       quote_volume=float(r[7]))
        except (TypeError, ValueError, IndexError) as exc:
            raise DataError(f"unparseable kline row {i}: {r!r}") from exc
        vals = (c.open, c.high, c.low, c.close, c.volume, c.quote_volume)
        if any(not math.isfinite(v) for v in vals) or min(c.open, c.high, c.low, c.close) <= 0:
            raise DataError(f"non-positive or non-finite price in kline row {i}")
        if c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close):
            raise DataError(f"inconsistent OHLC in kline row {i}")
        if c.open_time % step != 0 or c.close_time != c.open_time + step - 1:
            raise DataError(f"kline row {i} timestamps do not match interval {interval}")
        out.append(c)
    validate_series(out)
    return out


def validate_series(candles: list[Candle]) -> None:
    for a, b in zip(candles, candles[1:]):
        if b.open_time == a.open_time:
            raise DataError(f"duplicate candle at {iso(a.open_time)}")
        if b.open_time < a.open_time:
            raise DataError(f"candles out of order at {iso(b.open_time)}")


def closed_only(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Keep only candles that had fully closed before now_ms."""
    return [c for c in candles if c.close_time < now_ms]


def _trim_for_snapshot(tag: str, payload):
    if isinstance(payload, dict):
        p = dict(payload)
        if tag.startswith("protocol_"):
            keep = {k: p.get(k) for k in ("name", "slug", "symbol", "mcap", "category", "gecko_id")}
            keep["tvl_tail"] = (p.get("tvl") or [])[-30:]
            return keep
        if "totalDataChart" in p:
            p["totalDataChart"] = (p.get("totalDataChart") or [])[-40:]
            p.pop("totalDataChartBreakdown", None)
            p.pop("protocols", None)
        return p
    return payload


class LiveSource:
    """Binance SPOT (data-api.binance.vision) + DefiLlama free API. GET only."""

    name = "binance-spot-public+defillama"

    def __init__(self, snapshot_dir: Path | None = None,
                 http_get: Callable = get_json):
        self.snapshot_dir = snapshot_dir
        self.http_get = http_get

    def _get(self, url: str, tag: str) -> Observation:
        payload, retrieved = self.http_get(url)
        obs = Observation(source_url=url, retrieved_ms=retrieved, payload=payload)
        self._snapshot(tag, obs)
        return obs

    def _snapshot(self, tag: str, obs: Observation) -> None:
        if self.snapshot_dir is None:
            return
        day_dir = Path(self.snapshot_dir) / utc_date(obs.retrieved_ms)
        day_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)
        path = day_dir / f"{safe}_{obs.retrieved_ms}.json"
        doc = {"source_url": obs.source_url, "retrieved_utc": iso(obs.retrieved_ms),
               "payload": _trim_for_snapshot(tag, obs.payload)}
        path.write_text(json.dumps(doc, indent=1))

    # ---- Binance -------------------------------------------------------
    def symbol_status(self, symbol: str) -> Observation:
        obs = self._get(f"{BINANCE}/api/v3/exchangeInfo?symbol={quote(symbol)}",
                        f"exchangeinfo_{symbol}")
        syms = (obs.payload or {}).get("symbols") or []
        if len(syms) != 1 or syms[0].get("symbol") != symbol:
            raise DataError(f"exchangeInfo did not return exactly symbol {symbol}")
        obs.payload = {"symbol": syms[0]["symbol"], "status": syms[0].get("status"),
                       "quoteAsset": syms[0].get("quoteAsset"),
                       "isSpotTradingAllowed": syms[0].get("isSpotTradingAllowed")}
        return obs

    def klines_range(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> KlineResult:
        step = INTERVAL_MS[interval]
        out: list[Candle] = []
        urls: list[str] = []
        cur = start_ms - (start_ms % step)
        retrieved = 0
        while cur <= end_ms:
            url = (f"{BINANCE}/api/v3/klines?symbol={quote(symbol)}&interval={interval}"
                   f"&startTime={cur}&endTime={end_ms}&limit={KLINE_LIMIT}")
            obs = self._get(url, f"klines_{symbol}_{interval}")
            rows = parse_klines(obs.payload, interval)
            urls.append(url)
            retrieved = obs.retrieved_ms
            if not rows:
                break
            out.extend(rows)
            if len(rows) < KLINE_LIMIT:
                break
            cur = rows[-1].open_time + step
        validate_series(out)
        return KlineResult(candles=out, source_urls=urls, retrieved_ms=retrieved)

    # ---- DefiLlama -----------------------------------------------------
    def protocol(self, slug: str) -> Observation | None:
        try:
            obs = self._get(f"{LLAMA}/protocol/{quote(slug)}", f"protocol_{slug}")
        except NotAvailable:
            return None
        p = obs.payload if isinstance(obs.payload, dict) else {}
        obs.payload = {k: p.get(k) for k in ("name", "slug", "symbol", "mcap", "category")}
        obs.payload["tvl"] = (p.get("tvl") or [])[-30:]
        return obs

    def fees(self, slug: str, data_type: str) -> Observation | None:
        """data_type: dailyFees | dailyRevenue | dailyHoldersRevenue."""
        url = f"{LLAMA}/summary/fees/{quote(slug)}?dataType={data_type}"
        try:
            return self._get(url, f"fees_{slug}_{data_type}")
        except NotAvailable:
            return None
