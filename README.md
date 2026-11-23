# Seven-Role Paper Research Desk

A **local, paper-only** crypto research workflow. It runs a transparent 5-day
**forward** test with a $250 virtual balance on one or two Binance SPOT USDT
pairs and compares the result with buying and holding the same coins.

> **Safety:** there are no wallets, API keys, order endpoints or live execution.
> The only network calls are HTTPS `GET`s to an allowlist of public market-data
> paths (`deskcore/http.py`). Everything else is refused, including any path
> that contains `order`, `account` or `withdraw`.
>
> This is an educational tool, not financial advice. Nothing here reproduces or
> predicts any third party's reported returns.

## The seven roles

The roles are plain deterministic Python functions, **not** autonomous AI agents.
Every scan prints every role's verdict (`PASS` / `REJECT` / `UNKNOWN`) and its reasons.
A paper proposal needs all six research roles to PASS.

| # | Role | File | What it checks |
|---|---|---|---|
| 1 | Universe | `roles/universe.py` | Verified mapping row, symbol `TRADING` on SPOT, ordered/gap-free closed 1h candles, candle freshness, 24h quote volume |
| 2 | Valuation | `roles/valuation.py` | DefiLlama TVL (fresh), mcap/TVL when available, ticker matches DefiLlama symbol; gaps marked UNKNOWN |
| 3 | Unlock Watch | `roles/unlock_watch.py` | Manually verified `config/unlocks.csv`. A missing or stale source → SKIP. A confirmed unlock within 7 days → BLOCK |
| 4 | Revenue Analyst | `roles/revenue.py` | DefiLlama fees, protocol revenue and holder revenue, reported **separately**. No protocol revenue → SKIP. Protocol revenue is never treated as holder income |
| 5 | Signal | `roles/signal.py` | LONG-only sample rule on **closed** 1h candles: close > prior 20 highs, SMA50 rising, ATR14; stop = close − 1.5·ATR, target = close + 2·risk |
| 6 | Risk/Sizing | `roles/risk_sizing.py` | Max loss ≤ 1% of equity **including** fees and slippage, no leverage, one position (pending counts), 3% daily realized-loss stop, experiment window |
| 7 | Paper Executor/Auditor | `roles/executor.py` | Scan saves a PENDING proposal only. `paper-step` fills it from the first **observed 1m price after the signal** (within 1h, otherwise UNFILLED). Exits use later closed 1m candles; stop-first if both are touched |

## Setup

Requires Python 3.11+. The code uses only the standard library; `pytest` is needed for the tests.

**Windows (PowerShell)**
```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install -r requirements.txt
py -3 -m pytest -q
py -3 desk.py smoke-test
```

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pytest -q
python3 desk.py smoke-test
```

## Running the forward test

1. Fill in `config/asset_map.csv` and `config/unlocks.csv` with facts you verified
   yourself (see `config/README.md`). Review `config/settings.json`.
2. Check connectivity and data:
   ```
   python3 desk.py check-data
   ```
3. Run the first scan. **This starts the 5-day UTC timer**, which is saved in
   `state/desk_state.json` and never resets.
   ```
   python3 desk.py scan
   ```
4. After a scan proposes a trade, run `paper-step` within the hour so the fill uses a
   fresh observed price. Then run `scan` and `paper-step` at the same times each day,
   for example every few hours:
   ```
   python3 desk.py paper-step
   python3 desk.py scan
   ```
5. Run the report at any time, and once after the window ends:
   ```
   python3 desk.py report
   ```

Nothing runs on its own. You run the commands yourself, or set up your own
scheduler (Task Scheduler / cron). This repository does not schedule anything for you.

Exit codes: `0` ok, `1` check-data found problems, `2` **stopped fail-closed**
(network, parse or timestamp failure; existing records are left unchanged).

## Outputs

| File | Contents |
|---|---|
| `output/decisions.csv` | Every role verdict for every scan, with reasons, source URLs, retrieval times and data age (`data_json`) |
| `output/paper_trades.csv` | Append-only events: `PROPOSED`, `FILLED`, `UNFILLED`, `CLOSED_STOP`, `CLOSED_TARGET`, with observed prices, slippage, fees and net P&L |
| `output/daily_equity.csv` | Equity snapshot at every `paper-step` / `report` (used for drawdown) |
| `output/hold.csv` | `HOLD_ENTRY` at the first observed 1m price after the start; `HOLD_MARK` at each report |
| `output/report_FORWARD_latest.md` | Latest report text |
| `data/snapshots/YYYY-MM-DD/*.json` | Trimmed raw source payloads with `source_url` and `retrieved_utc` |
| `state/desk_state.json` | Timer, cash, pending/open position and the frozen rules |

Every CSV row has a `run_label` column: `FORWARD` for real runs and `SYNTHETIC`
for the smoke test, which writes only to `output/SYNTHETIC_smoke/`.

## How fills, exits and the hold comparison work

- **Entry:** uses the open of the first 1m candle that opens at or after the
  proposal time (so after the signal candle closed). Entry slippage (+0.05%) and
  fee (0.10%) are added. The position is re-sized at that price so the stop-out
  loss stays ≤ 1%. If there is no quote within 60 min, or the price is already
  past the stop or target, the proposal is UNFILLED.
- **Exit:** walks closed 1m candles after entry. If the stop is touched, the exit is
  the lower of stop and candle open, minus slippage and fee. If the target is
  touched, the exit is the target minus slippage and fee. If one candle touches
  both, the stop is assumed to have hit first.
- **End of window:** positions that haven't hit their stop or target stay **OPEN**.
  They aren't force-closed; the report shows their unrealized P&L separately,
  net of modeled exit costs.
- **Hold baseline:** $250 split equally across the same assets, bought at the
  first observed 1m open at or after the experiment start, with the same costs.
  It's marked at the same `as_of` time as the desk.

## Limitations (read these)

- Five days and a handful of trades are **far too few** to show an edge. Treat
  any result as a process check, not evidence.
- Fees and slippage are illustrative. Real fills, order-book depth, lot-size
  rounding (quantities are floored to 8 decimals here) and partial fills aren't modeled.
- 1m candles hide the path inside the minute. The stop-first rule is conservative,
  but it's still an approximation.
- DefiLlama metrics can be revised, delayed or missing. Missing data means SKIP,
  which can mean very few trades.
- The signal is a sample teaching rule, not a recommendation.
- `data-api.binance.vision` may be unavailable in some regions. If it fails, the
  desk stops. Add a different **public** data source rather than filling prices by hand.

## Starting a new experiment

Keep the old one: move `output/` and `state/` into an archive folder (for example
`archive/2026-11-23/`). Then edit `config/settings.json` and run `scan` again.
Don't edit or delete rows in an experiment's CSVs.

## Tests

`python3 -m pytest -q` runs deterministic tests on SYNTHETIC fixtures only. They check that:
a missing unlock source blocks; future candles can't enter a signal; position risk includes
costs; the one-position cap and the daily stop work; no non-allowlisted or order endpoint
can be called (including a full scan through the live source with a recording
transport); a skipped setup never gets P&L; unfilled proposals and unknown exits stay
OPEN; the hold baseline uses identical dates; and data failures stop the run without
changing existing records.
