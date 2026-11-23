# Config files

Fill these in **by hand** with facts you verified yourself. The desk never
invents a mapping or an unlock date. If a row is missing, the asset is SKIPPED.

## asset_map.csv: 1 or 2 rows

| column | meaning |
|---|---|
| `ticker` | Token ticker, e.g. `AAVE`. Must match DefiLlama's `symbol`, or Valuation rejects it as a contradictory mapping. |
| `binance_symbol` | Exact Binance **SPOT** pair, e.g. `AAVEUSDT`. Only USDT pairs are allowed. |
| `defillama_slug` | DefiLlama protocol slug, e.g. `aave` (the last part of `defillama.com/protocol/<slug>`). |
| `verification_url` | The page where you confirmed the token ↔ protocol relationship. |
| `verified_utc` | Date you checked, e.g. `2026-11-23`. |
| `notes` | Free text. |

Example (verify every value yourself before you use it):

```
ticker,binance_symbol,defillama_slug,verification_url,verified_utc,notes
AAVE,AAVEUSDT,aave,https://defillama.com/protocol/aave,2026-11-23,checked token page + DefiLlama
```

## unlocks.csv: at least one row per ticker

| column | meaning |
|---|---|
| `ticker` | Matches `asset_map.ticker`. |
| `status` | `scheduled` (a dated unlock) or `none_scheduled` (the primary source states no scheduled unlocks). Any other value is rejected. |
| `next_unlock_utc` | Required when `status=scheduled`, e.g. `2026-12-05T00:00:00Z`. Use `Z` for UTC. |
| `unlock_pct_supply` | Optional, for your notes. |
| `source_url` | **Required.** The issuer's announcement or a credible unlock calendar. |
| `checked_utc` | **Required.** When you checked. Evidence older than `unlock_check_max_age_days` (7) is rejected. |
| `notes` | Free text. |

Rules applied by Unlock Watch:
- no row, no source URL or no checked date → **SKIP**
- a scheduled unlock within 7 days → **BLOCK**
- only past dates on file → **UNKNOWN** (update the calendar) → SKIP

Lines that start with `#` are ignored.

## settings.json

Risk rules, cost assumptions and signal parameters. These rules are **frozen into
`state/desk_state.json` when the first scan starts the experiment**. Editing
settings.json mid-run doesn't change the running experiment. To test new rules,
start a new experiment (see the main README).
