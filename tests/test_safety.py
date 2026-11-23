"""No live-order endpoint can be called; only read-only GETs. SYNTHETIC fixtures."""
import re
from pathlib import Path

import pytest
from conftest import T0

from deskcore import http
from deskcore.pipeline import Context, check_data, scan
from deskcore.sources import LiveSource
from deskcore.synthetic import build_world

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("url", [
    "https://data-api.binance.vision/api/v3/order",
    "https://data-api.binance.vision/api/v3/order/test",
    "https://api.binance.com/api/v3/order",
    "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h",
    "https://data-api.binance.vision/api/v3/account",
    "https://data-api.binance.vision/sapi/v1/capital/withdraw/apply",
    "http://data-api.binance.vision/api/v3/klines",
    "https://evil.example.com/api/v3/klines",
])
def test_forbidden_urls_refused(url):
    with pytest.raises(PermissionError):
        http.check_url(url)


def test_market_data_urls_allowed():
    http.check_url("https://data-api.binance.vision/api/v3/klines?symbol=AAVEUSDT&interval=1h")
    http.check_url("https://api.llama.fi/summary/fees/aave?dataType=dailyRevenue")


def test_source_code_has_no_trading_capability():
    pattern = re.compile(r"method\s*=\s*['\"](POST|PUT|DELETE)|/api/v3/order|X-MBX-APIKEY|"
                         r"hmac|private_key|--dangerously", re.I)
    offenders = []
    for py in ROOT.rglob("*.py"):
        if "tests" in py.parts or ".venv" in py.parts:
            continue
        if pattern.search(py.read_text()):
            offenders.append(str(py))
    assert offenders == []


def test_live_source_only_issues_allowlisted_gets(tmp_path):
    """Drive the LIVE source through a full scan with a recording fake transport."""
    clock, syn, _ = build_world(tmp_path, T0)
    seen = []

    def fake_get(url):
        http.check_url(url)  # would raise for anything non-allowlisted
        seen.append(url)
        if "exchangeInfo" in url:
            sym = url.split("symbol=")[1]
            return {"symbols": [{"symbol": sym, "status": "TRADING", "quoteAsset": "USDT"}]}, clock()
        if "/klines" in url:
            sym = url.split("symbol=")[1].split("&")[0]
            q = dict(p.split("=") for p in url.split("?")[1].split("&"))
            res = syn.klines_range(sym, q["interval"], int(q["startTime"]), int(q["endTime"]))
            return [[c.open_time, c.open, c.high, c.low, c.close, c.volume, c.close_time,
                     c.quote_volume] for c in res.candles], clock()
        if "/protocol/" in url:
            return syn.protocols[url.rsplit("/", 1)[1]], clock()
        if "/summary/fees/" in url:
            slug = url.split("/summary/fees/")[1].split("?")[0]
            dt = url.split("dataType=")[1]
            if (slug, dt) not in syn.fee_data:
                raise http.NotAvailable("404")
            return syn.fee_data[(slug, dt)], clock()
        raise AssertionError(url)

    live = LiveSource(snapshot_dir=tmp_path / "data" / "snapshots", http_get=fake_get)
    ctx = Context(root=tmp_path, source=live, now_fn=clock, label="SYNTHETIC", out=lambda *_: None)
    assert check_data(ctx)
    summary = scan(ctx)
    assert summary[0]["final"] == "PROPOSE"
    assert seen and all(u.startswith("https://data-api.binance.vision/api/v3/") or
                        u.startswith("https://api.llama.fi/") for u in seen)
    assert not any("order" in u.lower() for u in seen)
    assert list((tmp_path / "data" / "snapshots").rglob("*.json"))  # snapshots recorded
