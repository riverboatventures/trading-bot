"""Role 3 - Unlock Watch: blocks missing, stale or near-term token-unlock evidence.

Evidence comes ONLY from the manually maintained config/unlocks.csv. A missing
row never defaults to 'clear'.

status values:
  scheduled      - next_unlock_utc is a primary-source unlock date
  none_scheduled - the primary source states there are no scheduled unlocks
"""
from __future__ import annotations

from deskcore.models import PASS, REJECT, UNKNOWN, RoleResult
from deskcore.timeutil import DAY_MS, iso, parse_iso

ROLE = "3-Unlock Watch"


def evaluate(asset: dict, unlock_rows: list[dict], now_ms: int, s: dict) -> RoleResult:
    ticker = asset.get("ticker", "").upper()
    rows = [r for r in unlock_rows if r.get("ticker", "").upper() == ticker]
    if not rows:
        return RoleResult(ROLE, REJECT, [f"no unlock evidence for {ticker} in unlocks.csv -> SKIP"])

    block_ms = s["unlock_block_days"] * DAY_MS
    max_check_age = s["unlock_check_max_age_days"] * DAY_MS
    future: list[int] = []
    none_scheduled = False
    data: dict = {}

    for r in rows:
        if not r.get("source_url", "").startswith("http"):
            return RoleResult(ROLE, REJECT, [f"unlock row for {ticker} has no primary-source URL -> SKIP"])
        try:
            checked = parse_iso(r.get("checked_utc", ""))
        except ValueError:
            return RoleResult(ROLE, REJECT, [f"unlock row for {ticker} has no valid checked_utc -> SKIP"])
        if checked > now_ms:
            return RoleResult(ROLE, REJECT, [f"unlock checked_utc {iso(checked)} is in the future"])
        if now_ms - checked > max_check_age:
            return RoleResult(ROLE, REJECT,
                              [f"unlock evidence checked {iso(checked)} is older than "
                               f"{s['unlock_check_max_age_days']} days -> re-verify"])
        status = r.get("status", "").lower()
        if status == "none_scheduled":
            none_scheduled = True
        elif status == "scheduled":
            try:
                t = parse_iso(r.get("next_unlock_utc", ""))
            except ValueError:
                return RoleResult(ROLE, REJECT, [f"scheduled unlock for {ticker} has no valid date -> SKIP"])
            if t >= now_ms:
                future.append(t)
        else:
            return RoleResult(ROLE, REJECT, [f"unknown unlock status '{r.get('status')}' -> SKIP"])

    if future:
        nxt = min(future)
        data["next_unlock_utc"] = iso(nxt)
        data["days_to_unlock"] = round((nxt - now_ms) / DAY_MS, 2)
        if nxt - now_ms <= block_ms:
            return RoleResult(ROLE, REJECT,
                              [f"confirmed unlock {iso(nxt)} within {s['unlock_block_days']} days -> BLOCK"],
                              data)
        return RoleResult(ROLE, PASS, [f"next unlock {iso(nxt)} ({data['days_to_unlock']} days away)"], data)
    if none_scheduled:
        return RoleResult(ROLE, PASS, ["primary source states no scheduled unlocks"], data)
    return RoleResult(ROLE, UNKNOWN,
                      ["only past unlock dates on file; next unlock unknown -> update calendar, SKIP"], data)
