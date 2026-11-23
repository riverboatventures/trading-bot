"""Read-only HTTP client.

Only HTTPS GET requests to an explicit allowlist of public market-data paths
are possible. There is no code path for POST, signed requests, API keys,
order, account or withdrawal endpoints.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .models import DataError, NotAvailable
from .timeutil import utcnow_ms

ALLOWED_PATHS = {
    "data-api.binance.vision": ("/api/v3/klines", "/api/v3/exchangeInfo", "/api/v3/ping"),
    "api.llama.fi": ("/protocols", "/protocol/", "/summary/fees/"),
}

# Defence in depth: refuse anything that even looks like a trading/account path.
FORBIDDEN_FRAGMENTS = ("order", "withdraw", "account", "userdatastream", "sapi", "margin",
                       "futures", "signature", "apikey", "deposit", "transfer")

USER_AGENT = "paper-research-desk/1.0 (read-only)"


def check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise PermissionError(f"refused non-HTTPS URL: {url}")
    host = parsed.hostname or ""
    if host not in ALLOWED_PATHS:
        raise PermissionError(f"refused host not on read-only allowlist: {host}")
    if not any(parsed.path == p or parsed.path.startswith(p) for p in ALLOWED_PATHS[host]):
        raise PermissionError(f"refused path not on read-only allowlist: {parsed.path}")
    lowered = (parsed.path + "?" + parsed.query).lower()
    for frag in FORBIDDEN_FRAGMENTS:
        if frag in lowered:
            raise PermissionError(f"refused URL containing forbidden fragment '{frag}': {url}")


def get_json(url: str, timeout: float = 20.0):
    """GET a public JSON document. Returns (payload, retrieved_ms)."""
    check_url(url)
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT,
                                                             "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404):
            raise NotAvailable(f"HTTP {exc.code} from {url}") from exc
        raise DataError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataError(f"network failure for {url}: {exc}") from exc
    retrieved = utcnow_ms()
    try:
        return json.loads(body), retrieved
    except ValueError as exc:
        raise DataError(f"could not parse JSON from {url}") from exc
