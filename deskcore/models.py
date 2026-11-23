"""Shared data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PASS = "PASS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"


class DataError(Exception):
    """Network, parse or timestamp failure. The desk stops instead of guessing."""


class NotAvailable(Exception):
    """The source answered, but has no data for this item (e.g. HTTP 404)."""


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float


@dataclass
class Observation:
    """One fetched payload with its provenance."""

    source_url: str
    retrieved_ms: int
    payload: Any


@dataclass
class KlineResult:
    candles: list[Candle]
    source_urls: list[str]
    retrieved_ms: int


@dataclass
class RoleResult:
    role: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == PASS
