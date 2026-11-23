import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from deskcore.storage import DEFAULT_SETTINGS  # noqa: E402

T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z


@pytest.fixture
def settings():
    return dict(DEFAULT_SETTINGS)


@pytest.fixture
def asset():
    return {"ticker": "SYNA", "binance_symbol": "SYNAUSDT", "defillama_slug": "syn-a",
            "verification_url": "synthetic://v", "verified_utc": "2026-01-01", "notes": "SYNTHETIC"}
