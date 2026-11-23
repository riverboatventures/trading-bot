"""Role 4 - Revenue Analyst: DefiLlama fees vs protocol revenue vs holder revenue.

Protocol fees and protocol revenue are NOT token-holder income. Holder revenue
is reported separately and marked UNKNOWN when DefiLlama has none.
"""
from __future__ import annotations

from deskcore.models import PASS, UNKNOWN, Observation, RoleResult
from deskcore.timeutil import DAY_MS, iso

ROLE = "4-Revenue Analyst"


def _metric(obs: Observation | None, now_ms: int, max_age_days: float):
    """Return (dict, problem_or_None)."""
    if obs is None:
        return {"value_24h": "UNKNOWN", "value_30d": "UNKNOWN"}, "not reported"
    p = obs.payload or {}
    out = {"source": obs.source_url, "retrieved_utc": iso(obs.retrieved_ms),
           "value_24h": p.get("total24h"), "value_30d": p.get("total30d")}
    chart = p.get("totalDataChart") or []
    if not chart:
        return out, "no data series"
    try:
        last_ms = int(chart[-1][0]) * 1000
    except (TypeError, ValueError, IndexError):
        return out, "unparseable data series"
    age = (now_ms - last_ms) / DAY_MS
    out["latest_point_utc"] = iso(last_ms)
    if age > max_age_days:
        return out, f"latest point {age:.1f} days old (stale)"
    if not isinstance(out["value_30d"], (int, float)):
        return out, "30d total missing"
    return out, None


def evaluate(asset: dict, fees: Observation | None, revenue: Observation | None,
             holders: Observation | None, now_ms: int, s: dict) -> RoleResult:
    max_age = s["revenue_max_age_days"]
    f, f_err = _metric(fees, now_ms, max_age)
    r, r_err = _metric(revenue, now_ms, max_age)
    h, h_err = _metric(holders, now_ms, max_age)
    data = {"fees": f, "protocol_revenue": r, "holders_revenue": h}
    reasons = []
    if f_err:
        reasons.append(f"fees UNKNOWN ({f_err})")
    if h_err:
        data["holders_revenue"]["value_30d"] = "UNKNOWN"
        reasons.append(f"token-holder revenue UNKNOWN ({h_err}); not inferred from protocol revenue")
    else:
        reasons.append(f"token-holder revenue 30d ${h['value_30d']:,.0f}")
    if r_err:
        return RoleResult(ROLE, UNKNOWN, [f"protocol revenue evidence missing ({r_err}) -> SKIP"] + reasons, data)
    if r["value_30d"] <= 0:
        return RoleResult(ROLE, UNKNOWN, ["protocol revenue 30d is zero -> SKIP"] + reasons, data)
    reasons.insert(0, f"protocol revenue 30d ${r['value_30d']:,.0f} (protocol income, not holder income)")
    return RoleResult(ROLE, PASS, reasons, data)
