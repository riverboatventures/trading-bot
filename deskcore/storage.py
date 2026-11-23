"""Config loading, append-only CSV journals and atomic JSON state."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .models import DataError

DEFAULT_SETTINGS = {
    "starting_cash_usd": 250.0,
    "experiment_days": 5,
    "max_assets": 2,
    "risk_per_trade_pct": 1.0,
    "max_daily_realized_loss_pct": 3.0,
    "max_open_positions": 1,
    "max_notional_pct_of_cash": 100.0,
    "fee_pct_per_side": 0.10,
    "slippage_pct_per_side": 0.05,
    "min_notional_usd": 10.0,
    "unlock_block_days": 7,
    "unlock_check_max_age_days": 7,
    "max_candle_age_minutes": 90,
    "min_quote_volume_24h_usd": 5_000_000.0,
    "tvl_max_age_days": 2,
    "revenue_max_age_days": 3,
    "fill_window_minutes": 60,
    "breakout_lookback": 20,
    "sma_period": 50,
    "atr_period": 14,
    "atr_stop_mult": 1.5,
    "reward_risk": 2.0,
    "warmup_candles": 120,
}

ASSET_MAP_COLUMNS = ["ticker", "binance_symbol", "defillama_slug", "verification_url",
                     "verified_utc", "notes"]
UNLOCK_COLUMNS = ["ticker", "status", "next_unlock_utc", "unlock_pct_supply", "source_url",
                  "checked_utc", "notes"]

CSV_SCHEMAS = {
    "decisions.csv": ["run_label", "scan_utc", "symbol", "role_order", "role", "verdict",
                      "reasons", "data_json", "final_decision"],
    "paper_trades.csv": ["run_label", "event_utc", "trade_id", "event", "symbol",
                         "signal_candle_close_utc", "observed_price_utc", "ref_price", "stop",
                         "target", "qty", "price_observed", "price_with_slippage", "fee_usd",
                         "slippage_usd", "pnl_net_usd", "cash_after", "reason", "source"],
    "daily_equity.csv": ["run_label", "as_of_utc", "utc_date", "cash", "position_symbol",
                         "position_qty", "mark_price", "mark_utc", "position_liq_value",
                         "equity", "realized_pnl_total", "day_realized_pnl"],
    "hold.csv": ["run_label", "event_utc", "event", "symbol", "price_utc", "price_observed",
                 "qty", "allocation_usd", "value_after_exit_costs", "cost_usd", "source"],
}


def load_settings(path: Path) -> dict:
    s = dict(DEFAULT_SETTINGS)
    if path.exists():
        raw = json.loads(path.read_text())
        for k, v in raw.items():
            if not k.startswith("_"):
                s[k] = v
    return s


def _read_config_csv(path: Path, columns: list[str]) -> list[dict]:
    if not path.exists():
        raise DataError(f"missing config file {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        return []
    missing = [c for c in columns if c not in reader.fieldnames]
    if missing:
        raise DataError(f"{path.name} is missing columns {missing}")
    return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def load_asset_map(path: Path) -> list[dict]:
    return _read_config_csv(path, ASSET_MAP_COLUMNS)


def load_unlocks(path: Path) -> list[dict]:
    return _read_config_csv(path, UNLOCK_COLUMNS)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.output = self.root / "output"
        self.state_path = self.root / "state" / "desk_state.json"

    # ---- CSV journals (append-only) -----------------------------------
    def append(self, name: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = CSV_SCHEMAS[name]
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            with path.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh), [])
            if header != cols:
                raise DataError(f"{path} has an unexpected header; refusing to modify it")
            new = False
        else:
            new = True
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="raise")
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({c: _fmt(r.get(c, "")) for c in cols})

    def read(self, name: str) -> list[dict]:
        path = self.output / name
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # ---- state ---------------------------------------------------------
    def load_state(self) -> dict | None:
        if not self.state_path.exists():
            return None
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        os.replace(tmp, self.state_path)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.10g}"
    return v
