"""Role 6 - Risk/Sizing: stop distance, max loss incl. costs, cash cap, exposure, daily stop."""
from __future__ import annotations

import math

from deskcore.models import PASS, REJECT, UNKNOWN, RoleResult

ROLE = "6-Risk/Sizing"


def cost_rates(s: dict) -> tuple[float, float]:
    return s["fee_pct_per_side"] / 100.0, s["slippage_pct_per_side"] / 100.0


def floor8(x: float) -> float:
    return math.floor(x * 1e8) / 1e8


def size_position(ref_price: float, stop: float, equity: float, cash: float, s: dict) -> dict:
    """Size so that a stop-out costs <= risk_per_trade_pct of equity INCLUDING
    entry/exit fees and entry/exit slippage."""
    fee, slip = cost_rates(s)
    entry_px = ref_price * (1 + slip)
    exit_px = stop * (1 - slip)
    per_unit_loss = entry_px * (1 + fee) - exit_px * (1 - fee)
    risk_budget = equity * s["risk_per_trade_pct"] / 100.0
    if per_unit_loss <= 0:
        return {"qty": 0.0, "reason": "non-positive per-unit loss"}
    qty_risk = risk_budget / per_unit_loss
    qty_cash = (cash * s["max_notional_pct_of_cash"] / 100.0) / (entry_px * (1 + fee))
    qty = floor8(min(qty_risk, qty_cash))
    return {
        "qty": qty,
        "entry_px_est": entry_px,
        "exit_px_at_stop_est": exit_px,
        "raw_stop_distance": ref_price - stop,
        "per_unit_loss_incl_costs": per_unit_loss,
        "risk_budget_usd": risk_budget,
        "max_loss_usd": qty * per_unit_loss,
        "notional_usd": qty * entry_px,
        "cash_required_usd": qty * entry_px * (1 + fee),
        "capped_by": "cash" if qty_cash < qty_risk else "risk",
    }


def account_gates(account: dict, s: dict) -> list[str]:
    """Reasons that forbid ANY new entry right now (empty list = allowed)."""
    out = []
    if not account.get("experiment_active"):
        out.append(account.get("experiment_note") or "experiment window not active")
    if account["open_positions"] + account["pending_orders"] >= s["max_open_positions"]:
        out.append(f"one-position cap: {account['open_positions']} open + "
                   f"{account['pending_orders']} pending")
    limit = account["day_start_equity"] * s["max_daily_realized_loss_pct"] / 100.0
    if -account["day_realized_pnl"] >= limit - 1e-12:
        out.append(f"daily stop hit: realized {account['day_realized_pnl']:.2f} today "
                   f"(limit -{limit:.2f})")
    return out


def evaluate(signal_data: dict | None, account: dict, s: dict) -> RoleResult:
    gates = account_gates(account, s)
    if signal_data is None:
        return RoleResult(ROLE, UNKNOWN, ["no valid setup to size"] + gates)
    if gates:
        return RoleResult(ROLE, REJECT, gates)
    ref, stop, target = signal_data["reference_close"], signal_data["stop"], signal_data["target"]
    if not (0 < stop < ref < target):
        return RoleResult(ROLE, REJECT, ["stop/target not on correct sides of reference price"])
    sz = size_position(ref, stop, account["equity"], account["cash"], s)
    if sz["qty"] <= 0 or sz["notional_usd"] < s["min_notional_usd"]:
        return RoleResult(ROLE, REJECT,
                          [f"position too small (notional ${sz.get('notional_usd', 0):.2f} "
                           f"< ${s['min_notional_usd']})"], sz)
    if sz["max_loss_usd"] > sz["risk_budget_usd"] + 1e-9:
        return RoleResult(ROLE, REJECT, ["max loss exceeds risk budget"], sz)
    if sz["cash_required_usd"] > account["cash"] + 1e-9:
        return RoleResult(ROLE, REJECT, ["notional exceeds available virtual cash (no leverage)"], sz)
    return RoleResult(ROLE, PASS,
                      [f"qty {sz['qty']:.8g}, notional ${sz['notional_usd']:.2f}, max loss incl. costs "
                       f"${sz['max_loss_usd']:.2f} <= ${sz['risk_budget_usd']:.2f} "
                       f"(capped by {sz['capped_by']})"], sz)
