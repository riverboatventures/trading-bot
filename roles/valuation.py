"""Role 2 - Valuation: DefiLlama protocol metrics; gaps are marked UNKNOWN."""
from __future__ import annotations

from deskcore.models import PASS, REJECT, UNKNOWN, Observation, RoleResult
from deskcore.timeutil import DAY_MS, iso

ROLE = "2-Valuation"


def evaluate(asset: dict, protocol: Observation | None, now_ms: int, s: dict) -> RoleResult:
    if protocol is None:
        return RoleResult(ROLE, UNKNOWN,
                          [f"DefiLlama has no protocol '{asset.get('defillama_slug')}' -> SKIP"])
    p = protocol.payload or {}
    data = {"source": protocol.source_url, "retrieved_utc": iso(protocol.retrieved_ms),
            "protocol_name": p.get("name")}
    reasons: list[str] = []

    llama_symbol = (p.get("symbol") or "").strip()
    if llama_symbol and llama_symbol != "-":
        if llama_symbol.upper() != asset["ticker"].upper():
            return RoleResult(ROLE, REJECT,
                              [f"contradictory mapping: DefiLlama symbol {llama_symbol} != "
                               f"asset_map ticker {asset['ticker']}"], data)
    else:
        reasons.append("DefiLlama token symbol UNKNOWN; relying on user-verified asset_map")

    tvl_series = p.get("tvl") or []
    if not tvl_series:
        return RoleResult(ROLE, UNKNOWN, reasons + ["TVL series UNKNOWN -> SKIP"], data)
    last = tvl_series[-1]
    try:
        tvl = float(last["totalLiquidityUSD"])
        tvl_ms = int(last["date"]) * 1000
    except (KeyError, TypeError, ValueError):
        return RoleResult(ROLE, UNKNOWN, reasons + ["TVL point unparseable -> SKIP"], data)
    age_days = (now_ms - tvl_ms) / DAY_MS
    data.update(tvl_usd=round(tvl, 2), tvl_utc=iso(tvl_ms), tvl_age_days=round(age_days, 2))
    if tvl <= 0:
        return RoleResult(ROLE, UNKNOWN, reasons + ["TVL is zero/negative -> SKIP"], data)
    if age_days > s["tvl_max_age_days"]:
        return RoleResult(ROLE, UNKNOWN,
                          reasons + [f"TVL is {age_days:.1f} days old (stale) -> SKIP"], data)

    mcap = p.get("mcap")
    if isinstance(mcap, (int, float)) and mcap > 0:
        data["mcap_usd"] = round(float(mcap), 2)
        data["mcap_to_tvl"] = round(float(mcap) / tvl, 3)
        reasons.append(f"mcap/TVL = {data['mcap_to_tvl']}")
    else:
        data["mcap_usd"] = "UNKNOWN"
        data["mcap_to_tvl"] = "UNKNOWN"
        reasons.append("market cap UNKNOWN (not reported by DefiLlama)")

    reasons.insert(0, f"TVL ${tvl:,.0f} as of {iso(tvl_ms)}")
    return RoleResult(ROLE, PASS, reasons, data)
