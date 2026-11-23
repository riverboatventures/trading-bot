"""Role 1 - Universe: verified mapping, live SPOT symbol, fresh candles, volume."""
from __future__ import annotations

from deskcore.models import PASS, REJECT, UNKNOWN, Candle, Observation, RoleResult
from deskcore.timeutil import HOUR_MS, MIN_MS, iso, parse_iso

ROLE = "1-Universe"
REQUIRED_MAP_FIELDS = ("ticker", "binance_symbol", "defillama_slug", "verification_url",
                       "verified_utc")


def min_candles(s: dict) -> int:
    return max(s["breakout_lookback"], s["sma_period"], s["atr_period"]) + 1


def evaluate(asset: dict, status: Observation, candles: list[Candle], now_ms: int,
             s: dict) -> RoleResult:
    reasons: list[str] = []
    data: dict = {"status_source": status.source_url,
                  "status_retrieved_utc": iso(status.retrieved_ms)}

    missing = [f for f in REQUIRED_MAP_FIELDS if not asset.get(f)]
    if missing:
        return RoleResult(ROLE, REJECT, [f"asset_map row missing verified fields {missing}"], data)
    try:
        parse_iso(asset["verified_utc"])
    except ValueError:
        return RoleResult(ROLE, REJECT, ["asset_map verified_utc is not a valid UTC date"], data)

    st = status.payload or {}
    if st.get("symbol") != asset["binance_symbol"]:
        return RoleResult(ROLE, REJECT, ["exchange symbol does not match asset_map"], data)
    if st.get("status") != "TRADING":
        return RoleResult(ROLE, REJECT, [f"symbol status is {st.get('status')}, not TRADING"], data)
    if st.get("quoteAsset") not in (None, "USDT"):
        return RoleResult(ROLE, REJECT, [f"quote asset is {st.get('quoteAsset')}, not USDT"], data)

    if len(candles) < min_candles(s):
        return RoleResult(ROLE, UNKNOWN,
                          [f"only {len(candles)} closed 1h candles; need {min_candles(s)}"], data)
    for a, b in zip(candles, candles[1:]):
        if b.open_time - a.open_time != HOUR_MS:
            return RoleResult(ROLE, REJECT, [f"gap in 1h candles after {iso(a.close_time)}"], data)

    last = candles[-1]
    age_min = (now_ms - last.close_time) / MIN_MS
    data.update(last_close=last.close, last_close_utc=iso(last.close_time + 1),
                candle_age_min=round(age_min, 1))
    if age_min > s["max_candle_age_minutes"]:
        return RoleResult(ROLE, REJECT, [f"latest closed candle is {age_min:.0f} min old (stale)"],
                          data)

    qv = sum(c.quote_volume for c in candles[-24:])
    data["quote_volume_24h_usd"] = round(qv, 2)
    if qv < s["min_quote_volume_24h_usd"]:
        return RoleResult(ROLE, REJECT,
                          [f"24h quote volume ${qv:,.0f} below ${s['min_quote_volume_24h_usd']:,.0f}"],
                          data)

    reasons.append(f"{asset['binance_symbol']} TRADING, {len(candles)} closed 1h candles, "
                   f"last closed {age_min:.0f} min ago, 24h vol ${qv:,.0f}")
    return RoleResult(ROLE, PASS, reasons, data)
