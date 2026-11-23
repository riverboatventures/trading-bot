"""Orchestration for the CLI commands: check-data, scan, paper-step, report."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from roles import ROLE_ORDER, executor, revenue, risk_sizing, signal, universe, unlock_watch, valuation

from .models import PASS, DataError, RoleResult
from .sources import closed_only
from .storage import Store, load_asset_map, load_settings, load_unlocks
from .timeutil import DAY_MS, HOUR_MS, MIN_MS, day_start_ms, iso, utc_date, utcnow_ms


@dataclass
class Context:
    root: Path
    source: object
    now_fn: Callable[[], int] = utcnow_ms
    label: str = "FORWARD"
    out: Callable[[str], None] = print
    config_dir: Path | None = None
    lines: list[str] = field(default_factory=list)

    def say(self, msg: str = "") -> None:
        self.lines.append(msg)
        self.out(msg)

    @property
    def cfg(self) -> Path:
        return self.config_dir or (Path(self.root) / "config")

    @property
    def store(self) -> Store:
        return Store(self.root)


# --------------------------------------------------------------------------
# config + state
# --------------------------------------------------------------------------
def load_config(ctx: Context):
    s = load_settings(ctx.cfg / "settings.json")
    assets = load_asset_map(ctx.cfg / "asset_map.csv")
    unlocks = load_unlocks(ctx.cfg / "unlocks.csv")
    if not assets:
        raise DataError("config/asset_map.csv has no assets. Add 1-2 verified rows (see config/README.md).")
    if len(assets) > s["max_assets"]:
        raise DataError(f"asset_map has {len(assets)} assets; this experiment allows {s['max_assets']}")
    return s, assets, unlocks


def new_state(ctx: Context, s: dict, assets: list[dict], start_ms: int) -> dict:
    end_ms = start_ms + int(s["experiment_days"]) * DAY_MS
    cash = float(s["starting_cash_usd"])
    return {
        "version": 1, "label": ctx.label,
        "starting_cash": cash, "cash": cash, "realized_pnl": 0.0,
        "experiment": {"start_ms": start_ms, "end_ms": end_ms,
                       "start_utc": iso(start_ms), "end_utc": iso(end_ms)},
        "settings_at_start": s,
        "pending": None, "position": None, "next_trade_id": 1, "closed_trades": [],
        "hold": {"symbols": [a["binance_symbol"] for a in assets],
                 "alloc_each": cash / len(assets), "entries": {}},
    }


def load_state(ctx: Context, s: dict | None = None, assets: list[dict] | None = None,
               start_if_missing: bool = False) -> dict:
    state = ctx.store.load_state()
    if state is None:
        if not start_if_missing:
            raise DataError("no experiment state yet; run 'scan' first to start the 5-day UTC timer")
        state = new_state(ctx, s, assets, ctx.now_fn())
        ctx.store.save_state(state)
        ctx.say(f"Started 5-day UTC experiment: {state['experiment']['start_utc']} -> "
                f"{state['experiment']['end_utc']} (saved to state/desk_state.json)")
    if state.get("label") != ctx.label:
        raise DataError(f"state file belongs to run '{state.get('label')}', not '{ctx.label}'")
    return state


def account(state: dict, now_ms: int) -> dict:
    exp = state["experiment"]
    d0 = day_start_ms(now_ms)
    closed = state["closed_trades"]
    day_pnl = sum(t["pnl_net"] for t in closed if t["exit_ms"] >= d0)
    before = sum(t["pnl_net"] for t in closed if t["exit_ms"] < d0)
    active = exp["start_ms"] <= now_ms < exp["end_ms"]
    note = None if active else (
        "experiment ended at " + exp["end_utc"] if now_ms >= exp["end_ms"] else "experiment not started")
    return {
        "experiment_active": active, "experiment_note": note,
        "cash": state["cash"],
        "equity": state["starting_cash"] + state["realized_pnl"],
        "open_positions": 1 if state["position"] else 0,
        "pending_orders": 1 if state["pending"] else 0,
        "day_realized_pnl": day_pnl,
        "day_start_equity": state["starting_cash"] + before,
    }


# --------------------------------------------------------------------------
# check-data
# --------------------------------------------------------------------------
def check_data(ctx: Context) -> bool:
    s, assets, unlocks = load_config(ctx)
    now = ctx.now_fn()
    ok = True
    ctx.say(f"check-data @ {iso(now)} (source: {getattr(ctx.source, 'name', '?')})")
    for a in assets:
        sym = a["binance_symbol"]
        try:
            st = ctx.source.symbol_status(sym)
            ctx.say(f"  [{'OK' if st.payload.get('status') == 'TRADING' else 'FAIL'}] {sym} status "
                    f"{st.payload.get('status')}  ({st.source_url})")
            ok &= st.payload.get("status") == "TRADING"
            kl = ctx.source.klines_range(sym, "1h", now - 4 * HOUR_MS, now)
            closed = closed_only(kl.candles, now)
            if closed:
                age = (now - closed[-1].close_time) / MIN_MS
                fresh = age <= s["max_candle_age_minutes"]
                ok &= fresh
                ctx.say(f"  [{'OK' if fresh else 'FAIL'}] {sym} last closed 1h candle "
                        f"{iso(closed[-1].close_time + 1)} ({age:.0f} min old, close {closed[-1].close:g})")
            else:
                ok = False
                ctx.say(f"  [FAIL] {sym} returned no closed 1h candles")
            proto = ctx.source.protocol(a["defillama_slug"])
            ok &= proto is not None
            ctx.say(f"  [{'OK' if proto else 'FAIL'}] DefiLlama protocol '{a['defillama_slug']}'"
                    + (f" -> {proto.payload.get('name')} (symbol {proto.payload.get('symbol')})" if proto else " not found"))
            rev = ctx.source.fees(a["defillama_slug"], "dailyRevenue")
            ctx.say(f"  [{'OK' if rev else 'WARN'}] DefiLlama revenue for '{a['defillama_slug']}'"
                    + ("" if rev else " not available -> Revenue role will SKIP"))
        except DataError as exc:
            ok = False
            ctx.say(f"  [FAIL] {sym}: {exc}")
        has_unlock = any(u.get("ticker", "").upper() == a["ticker"].upper() for u in unlocks)
        ctx.say(f"  [{'OK' if has_unlock else 'WARN'}] unlocks.csv row for {a['ticker']}"
                + ("" if has_unlock else " missing -> Unlock Watch will SKIP"))
    ctx.say("check-data: " + ("ALL REQUIRED CHECKS PASSED" if ok else "FAILURES ABOVE"))
    return ok


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------
def _fetch_asset(ctx: Context, a: dict, s: dict, now: int) -> dict:
    sym, slug = a["binance_symbol"], a["defillama_slug"]
    status = ctx.source.symbol_status(sym)
    kl = ctx.source.klines_range(sym, "1h", now - (s["warmup_candles"] + 2) * HOUR_MS, now)
    return {
        "status": status, "klines": kl, "candles": closed_only(kl.candles, now),
        "protocol": ctx.source.protocol(slug),
        "fees": ctx.source.fees(slug, "dailyFees"),
        "revenue": ctx.source.fees(slug, "dailyRevenue"),
        "holders": ctx.source.fees(slug, "dailyHoldersRevenue"),
    }


def _print_roles(ctx: Context, header: str, results: list[RoleResult]) -> None:
    ctx.say(header)
    for r in results:
        ctx.say(f"  {r.role:<26} {r.verdict:<8} {' | '.join(r.reasons)}")


def scan(ctx: Context) -> list[dict]:
    s, assets, unlocks = load_config(ctx)
    now = ctx.now_fn()
    store = ctx.store
    # Fetch EVERYTHING first; on any failure stop without writing new decisions.
    fetched = {}
    try:
        for a in assets:
            fetched[a["binance_symbol"]] = _fetch_asset(ctx, a, s, now)
    except DataError as exc:
        store.append("decisions.csv", [{"run_label": ctx.label, "scan_utc": iso(now), "symbol": "*",
                                        "role_order": 0, "role": "DATA", "verdict": "DATA_FAIL",
                                        "reasons": str(exc), "data_json": "{}",
                                        "final_decision": "STOP"}])
        ctx.say(f"DATA FAILURE - scan stopped, no proposals made: {exc}")
        raise

    state = load_state(ctx, s, assets, start_if_missing=True)
    acct = account(state, now)
    ctx.say(f"scan @ {iso(now)}  [{ctx.label}]  experiment {state['experiment']['start_utc']} -> "
            f"{state['experiment']['end_utc']}  cash ${state['cash']:.2f}")
    summary = []
    for a in assets:
        sym = a["binance_symbol"]
        f = fetched[sym]
        results = [
            universe.evaluate(a, f["status"], f["candles"], now, s),
            valuation.evaluate(a, f["protocol"], now, s),
            unlock_watch.evaluate(a, unlocks, now, s),
            revenue.evaluate(a, f["fees"], f["revenue"], f["holders"], now, s),
        ]
        sig = signal.evaluate(f["candles"], now, s)
        sig.data["klines_source"] = f["klines"].source_urls[-1] if f["klines"].source_urls else ""
        sig.data["klines_retrieved_utc"] = iso(f["klines"].retrieved_ms)
        results.append(sig)
        results.append(risk_sizing.evaluate(sig.data if sig.passed else None, acct, s))
        results.append(executor.evaluate_scan(results))
        final = "PROPOSE" if results[-1].passed else "SKIP"

        if final == "PROPOSE":
            tid = state["next_trade_id"]
            state["next_trade_id"] += 1
            state["pending"] = {
                "trade_id": tid, "symbol": sym, "ticker": a["ticker"], "created_ms": now,
                "signal_close_ms": sig.data["signal_candle_close_ms"],
                "ref_price": sig.data["reference_close"], "stop": sig.data["stop"],
                "target": sig.data["target"], "planned_qty": results[5].data["qty"],
            }
            acct["pending_orders"] = 1
            store.append("paper_trades.csv", [{
                "run_label": ctx.label, "event_utc": iso(now), "trade_id": tid, "event": "PROPOSED",
                "symbol": sym, "signal_candle_close_utc": sig.data["signal_candle_close_utc"],
                "ref_price": sig.data["reference_close"], "stop": sig.data["stop"],
                "target": sig.data["target"], "qty": results[5].data["qty"], "cash_after": state["cash"],
                "reason": "research proposal only; awaiting later observed price",
                "source": sig.data["klines_source"]}])

        _print_roles(ctx, f"\n=== {sym} ({a['ticker']}) -> {final} ===", results)
        store.append("decisions.csv", [{
            "run_label": ctx.label, "scan_utc": iso(now), "symbol": sym, "role_order": i + 1,
            "role": r.role, "verdict": r.verdict, "reasons": " | ".join(r.reasons),
            "data_json": json.dumps(r.data, default=str, sort_keys=True), "final_decision": final}
            for i, r in enumerate(results)])
        summary.append({"symbol": sym, "final": final, "results": results})
    store.save_state(state)
    return summary


# --------------------------------------------------------------------------
# paper-step
# --------------------------------------------------------------------------
def _trade_row(ctx, now, tid, event, sym, **kw) -> dict:
    row = {"run_label": ctx.label, "event_utc": iso(now), "trade_id": tid, "event": event, "symbol": sym}
    row.update(kw)
    return row


def ensure_hold_entries(ctx: Context, state: dict, now: int, s: dict) -> list[dict]:
    rows = []
    hold = state["hold"]
    start = state["experiment"]["start_ms"]
    fee, slip = risk_sizing.cost_rates(s)
    for sym in hold["symbols"]:
        if sym in hold["entries"]:
            continue
        kl = ctx.source.klines_range(sym, "1m", start, min(now, start + 6 * HOUR_MS))
        c = next((c for c in kl.candles if c.open_time >= start and c.open_time < now), None)
        if c is None:
            continue
        alloc = hold["alloc_each"]
        qty = risk_sizing.floor8(alloc / (c.open * (1 + slip) * (1 + fee)))
        hold["entries"][sym] = {"price": c.open, "price_ms": c.open_time, "qty": qty,
                                "cost": alloc - qty * c.open}
        rows.append({"run_label": ctx.label, "event_utc": iso(now), "event": "HOLD_ENTRY", "symbol": sym,
                     "price_utc": iso(c.open_time), "price_observed": c.open, "qty": qty,
                     "allocation_usd": alloc, "cost_usd": alloc - qty * c.open,
                     "source": kl.source_urls[0] if kl.source_urls else ""})
    ctx.store.append("hold.csv", rows)
    return rows


def paper_step(ctx: Context) -> dict:
    s = load_settings(ctx.cfg / "settings.json")
    state = load_state(ctx)
    s = {**s, **{k: v for k, v in state["settings_at_start"].items()}}
    now = ctx.now_fn()
    end = state["experiment"]["end_ms"]
    until = min(now, end)
    store = ctx.store
    events: list[dict] = []
    result = {"fill": None, "exit": None}
    ctx.say(f"paper-step @ {iso(now)}  [{ctx.label}]")

    p = state["pending"]
    if p:
        window_end = p["created_ms"] + s["fill_window_minutes"] * MIN_MS
        kl = ctx.source.klines_range(p["symbol"], "1m", p["created_ms"], min(now, window_end + MIN_MS))
        acct = account(state, now)
        res = executor.try_fill(p, kl.candles, now, acct, s, end)
        result["fill"] = res
        src = kl.source_urls[0] if kl.source_urls else ""
        if res["status"] == "FILLED":
            state["cash"] -= res["cash_out"]
            state["position"] = {**p, "qty": res["qty"], "entry_px": res["entry_px"],
                                 "entry_raw": res["price_observed"], "entry_ms": res["observed_price_ms"],
                                 "entry_fee": res["entry_fee"], "cash_out": res["cash_out"],
                                 "next_check_ms": res["observed_price_ms"],
                                 "mark_price": res["price_observed"], "mark_ms": res["observed_price_ms"]}
            state["pending"] = None
            events.append(_trade_row(ctx, now, p["trade_id"], "FILLED", p["symbol"],
                                     signal_candle_close_utc=iso(p["signal_close_ms"]),
                                     observed_price_utc=iso(res["observed_price_ms"]),
                                     ref_price=p["ref_price"], stop=p["stop"], target=p["target"],
                                     qty=res["qty"], price_observed=res["price_observed"],
                                     price_with_slippage=res["entry_px"], fee_usd=res["entry_fee"],
                                     slippage_usd=res["entry_slippage_usd"], cash_after=state["cash"],
                                     reason=res["reason"], source=src))
        elif res["status"] == "UNFILLED":
            state["pending"] = None
            events.append(_trade_row(ctx, now, p["trade_id"], "UNFILLED", p["symbol"],
                                     signal_candle_close_utc=iso(p["signal_close_ms"]),
                                     observed_price_utc=iso(res.get("observed_price_ms")),
                                     ref_price=p["ref_price"], stop=p["stop"], target=p["target"],
                                     price_observed=res.get("price_observed", ""), pnl_net_usd=0.0,
                                     cash_after=state["cash"], reason=res["reason"], source=src))
        ctx.say(f"  pending #{p['trade_id']} {p['symbol']}: {res['status']} - {res['reason']}")

    pos = state["position"]
    if pos and pos["next_check_ms"] < until:
        kl = ctx.source.klines_range(pos["symbol"], "1m", pos["next_check_ms"], until)
        res = executor.advance(pos, kl.candles, until, s)
        result["exit"] = res
        if res["status"] == "CLOSED":
            state["cash"] += res["cash_in"]
            state["realized_pnl"] += res["pnl_net"]
            state["closed_trades"].append({"trade_id": pos["trade_id"], "symbol": pos["symbol"],
                                           "exit_ms": res["exit_ms"], "pnl_net": res["pnl_net"]})
            state["position"] = None
            events.append(_trade_row(ctx, now, pos["trade_id"], res["event"], pos["symbol"],
                                     signal_candle_close_utc=iso(pos["signal_close_ms"]),
                                     observed_price_utc=iso(res["observed_price_ms"]),
                                     ref_price=pos["ref_price"], stop=pos["stop"], target=pos["target"],
                                     qty=pos["qty"], price_observed=res["price_observed"],
                                     price_with_slippage=res["exit_px"], fee_usd=res["exit_fee"],
                                     slippage_usd=res["exit_slippage_usd"], pnl_net_usd=res["pnl_net"],
                                     cash_after=state["cash"], reason=res["reason"],
                                     source=kl.source_urls[0] if kl.source_urls else ""))
            ctx.say(f"  position #{pos['trade_id']} {pos['symbol']}: {res['event']} "
                    f"net P&L ${res['pnl_net']:.2f} ({res['reason']})")
        else:
            if "next_check_ms" in res:
                pos.update(next_check_ms=res["next_check_ms"], mark_price=res["mark_price"],
                           mark_ms=res["mark_ms"])
            ctx.say(f"  position #{pos['trade_id']} {pos['symbol']}: OPEN, mark {pos['mark_price']:g} "
                    f"@ {iso(pos['mark_ms'])}")
    elif pos:
        ctx.say(f"  position #{pos['trade_id']} {pos['symbol']}: OPEN (no new closed candles in window)")
    if not p and not pos:
        ctx.say("  nothing pending or open")
    if now >= end:
        ctx.say(f"  experiment window ended {state['experiment']['end_utc']}; open positions stay OPEN, "
                f"no new entries")

    store.append("paper_trades.csv", events)
    ensure_hold_entries(ctx, state, now, s)
    store.append("daily_equity.csv", [_equity_row(ctx, state, now, s)])
    store.save_state(state)
    return result


def _equity_row(ctx: Context, state: dict, now: int, s: dict) -> dict:
    pos = state["position"]
    liq = executor.liquidation_value(pos["qty"], pos["mark_price"], s) if pos else 0.0
    acct = account(state, now)
    return {"run_label": ctx.label, "as_of_utc": iso(now), "utc_date": utc_date(now),
            "cash": state["cash"], "position_symbol": pos["symbol"] if pos else "",
            "position_qty": pos["qty"] if pos else 0, "mark_price": pos["mark_price"] if pos else "",
            "mark_utc": iso(pos["mark_ms"]) if pos else "", "position_liq_value": liq,
            "equity": state["cash"] + liq, "realized_pnl_total": state["realized_pnl"],
            "day_realized_pnl": acct["day_realized_pnl"]}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def _mark(ctx: Context, sym: str, as_of: int):
    kl = ctx.source.klines_range(sym, "1m", as_of - 15 * MIN_MS, as_of)
    closed = closed_only(kl.candles, as_of)
    if not closed:
        return None
    return closed[-1].close, closed[-1].close_time + 1


def max_drawdown(values: list[float]) -> float:
    peak, dd = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return dd


def report(ctx: Context) -> dict:
    s = load_settings(ctx.cfg / "settings.json")
    state = load_state(ctx)
    s = {**s, **state["settings_at_start"]}
    now = ctx.now_fn()
    exp = state["experiment"]
    as_of = min(now, exp["end_ms"])
    store = ctx.store
    trades = [r for r in store.read("paper_trades.csv") if r["run_label"] == ctx.label]
    decisions = [r for r in store.read("decisions.csv") if r["run_label"] == ctx.label]

    marks, mark_err = {}, None
    syms = set(state["hold"]["symbols"]) | ({state["position"]["symbol"]} if state["position"] else set())
    try:
        ensure_hold_entries(ctx, state, now, s)
        for sym in sorted(syms):
            m = _mark(ctx, sym, as_of)
            if m:
                marks[sym] = m
    except DataError as exc:
        mark_err = str(exc)

    closed = [t for t in trades if t["event"].startswith("CLOSED_")]
    pnls = [float(t["pnl_net_usd"]) for t in closed]
    costs = sum(float(t["fee_usd"] or 0) + float(t["slippage_usd"] or 0)
                for t in trades if t["event"] in ("FILLED", "CLOSED_STOP", "CLOSED_TARGET"))
    pos = state["position"]
    unreal, liq = 0.0, 0.0
    pos_line = "none"
    if pos:
        if pos["symbol"] in marks:
            pos["mark_price"], pos["mark_ms"] = marks[pos["symbol"]]
        liq = executor.liquidation_value(pos["qty"], pos["mark_price"], s)
        unreal = liq - pos["cash_out"]
        pos_line = (f"{pos['symbol']} qty {pos['qty']:.8g} entry {pos['entry_raw']:g} mark {pos['mark_price']:g} "
                    f"@ {iso(pos['mark_ms'])}; liq value ${liq:.2f}; unrealized ${unreal:.2f} (OPEN)")
    equity = state["cash"] + liq
    eq_rows = [r for r in store.read("daily_equity.csv") if r["run_label"] == ctx.label]
    dd = max_drawdown([state["starting_cash"]] + [float(r["equity"]) for r in eq_rows] + [equity])

    signal_pass_scans = {(d["scan_utc"], d["symbol"]) for d in decisions
                         if d["role"] == signal.ROLE and d["verdict"] == PASS}
    blocked = {(d["scan_utc"], d["symbol"]) for d in decisions
               if d["final_decision"] == "SKIP"} & signal_pass_scans
    skip_by_role: dict[str, int] = {}
    for d in decisions:
        if d["final_decision"] == "SKIP" and d["verdict"] != PASS and d["role"] != executor.ROLE:
            skip_by_role[d["role"]] = skip_by_role.get(d["role"], 0) + 1
    data_fails = sum(1 for d in decisions if d["verdict"] == "DATA_FAIL")

    hold = state["hold"]
    hold_val, hold_known = 0.0, True
    hold_lines, hold_rows = [], []
    for sym in hold["symbols"]:
        e = hold["entries"].get(sym)
        if not e or sym not in marks:
            hold_known = False
            hold_lines.append(f"    {sym}: UNKNOWN (entry or mark not observed)")
            continue
        price, mark_ms = marks[sym]
        v = executor.liquidation_value(e["qty"], price, s)
        hold_val += v
        hold_lines.append(f"    {sym}: bought {e['qty']:.8g} @ {e['price']:g} ({iso(e['price_ms'])}), "
                          f"mark {price:g} @ {iso(mark_ms)} -> ${v:.2f} after exit costs")
        hold_rows.append({"run_label": ctx.label, "event_utc": iso(now), "event": "HOLD_MARK", "symbol": sym,
                          "price_utc": iso(mark_ms), "price_observed": price, "qty": e["qty"],
                          "allocation_usd": hold["alloc_each"], "value_after_exit_costs": v,
                          "source": "binance 1m close"})
    store.append("hold.csv", hold_rows)

    status = "COMPLETE" if now >= exp["end_ms"] else "IN PROGRESS"
    L = []
    L.append(f"# Paper desk report [{ctx.label}] - {status}")
    if ctx.label != "FORWARD":
        L.append("**SYNTHETIC DATA - NOT A MARKET RESULT**")
    L.append(f"Window (UTC): {exp['start_utc']} -> {exp['end_utc']}; measured at {iso(as_of)}")
    L.append(f"Costs (illustrative): fee {s['fee_pct_per_side']}%/side, slippage {s['slippage_pct_per_side']}%/side")
    L.append("")
    L.append("## Desk")
    L.append(f"  Starting cash:          ${state['starting_cash']:.2f}")
    L.append(f"  Cash:                   ${state['cash']:.2f}")
    L.append(f"  Net realized P&L:       ${sum(pnls):.2f} (after modeled costs)")
    L.append(f"  Open position:          {pos_line}")
    L.append(f"  Unrealized P&L:         ${unreal:.2f} (net of modeled exit costs)")
    L.append(f"  Equity:                 ${equity:.2f}" + (" (marks UNKNOWN: " + mark_err + ")" if mark_err else ""))
    L.append(f"  Max drawdown:           {dd * 100:.2f}% (from recorded equity snapshots)")
    L.append(f"  Closed trades:          {len(closed)}  wins {sum(1 for p in pnls if p > 0)}  "
             f"losses {sum(1 for p in pnls if p <= 0)}")
    L.append(f"  Proposals / unfilled:   {sum(1 for t in trades if t['event'] == 'PROPOSED')} / "
             f"{sum(1 for t in trades if t['event'] == 'UNFILLED')}")
    L.append(f"  Modeled costs paid:     ${costs:.2f}"
             + (f" (avg ${costs / len(closed):.2f} per closed trade)" if closed else ""))
    L.append(f"  Blocked candidates:     {len(blocked)} (signal PASS but another role failed)")
    L.append(f"  Scans/data failures:    {len({d['scan_utc'] for d in decisions})} / {data_fails}")
    if skip_by_role:
        L.append("  Non-PASS verdicts on skipped scans: "
                 + ", ".join(f"{k} x{v}" for k, v in sorted(skip_by_role.items())))
    L.append("")
    L.append("## Equal-weight buy-and-hold, same assets, same window, same cost assumptions")
    L.extend(hold_lines)
    if hold_known:
        L.append(f"  Hold value: ${hold_val:.2f}  (P&L ${hold_val - state['starting_cash']:.2f})")
        L.append(f"  Desk minus hold: ${equity - hold_val:.2f}")
    else:
        L.append("  Hold value: UNKNOWN until every entry and mark is observed")
    text = "\n".join(L)
    for line in L:
        ctx.say(line)
    store.output.mkdir(parents=True, exist_ok=True)
    (store.output / f"report_{ctx.label}_latest.md").write_text(text + "\n")
    store.append("daily_equity.csv", [_equity_row(ctx, state, now, s)])
    store.save_state(state)
    return {"equity": equity, "realized": sum(pnls), "unrealized": unreal, "closed": len(closed),
            "hold_value": hold_val if hold_known else None, "as_of": as_of, "marks": marks,
            "blocked": len(blocked), "max_drawdown": dd, "status": status}
