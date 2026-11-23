"""A missing / stale / near-term unlock source must block. SYNTHETIC fixtures."""
from conftest import T0

from deskcore.models import PASS, REJECT, UNKNOWN
from deskcore.timeutil import DAY_MS, iso
from roles import unlock_watch


def row(**kw):
    r = {"ticker": "SYNA", "status": "none_scheduled", "next_unlock_utc": "", "unlock_pct_supply": "",
         "source_url": "https://example.invalid/src", "checked_utc": iso(T0 - DAY_MS), "notes": ""}
    r.update(kw)
    return r


def test_missing_unlock_row_blocks(asset, settings):
    assert unlock_watch.evaluate(asset, [], T0, settings).verdict == REJECT


def test_missing_unlock_source_blocks(asset, settings):
    assert unlock_watch.evaluate(asset, [row(source_url="")], T0, settings).verdict == REJECT


def test_missing_checked_date_blocks(asset, settings):
    assert unlock_watch.evaluate(asset, [row(checked_utc="")], T0, settings).verdict == REJECT


def test_stale_check_blocks(asset, settings):
    r = row(checked_utc=iso(T0 - 8 * DAY_MS))
    assert unlock_watch.evaluate(asset, [r], T0, settings).verdict == REJECT


def test_unlock_within_seven_days_blocks(asset, settings):
    r = row(status="scheduled", next_unlock_utc=iso(T0 + 6 * DAY_MS))
    assert unlock_watch.evaluate(asset, [r], T0, settings).verdict == REJECT


def test_unlock_beyond_seven_days_passes(asset, settings):
    r = row(status="scheduled", next_unlock_utc=iso(T0 + 10 * DAY_MS))
    assert unlock_watch.evaluate(asset, [r], T0, settings).verdict == PASS


def test_only_past_unlocks_is_unknown_not_clear(asset, settings):
    r = row(status="scheduled", next_unlock_utc=iso(T0 - 2 * DAY_MS))
    assert unlock_watch.evaluate(asset, [r], T0, settings).verdict == UNKNOWN


def test_unknown_status_blocks(asset, settings):
    assert unlock_watch.evaluate(asset, [row(status="probably_fine")], T0, settings).verdict == REJECT


def test_none_scheduled_with_source_passes(asset, settings):
    assert unlock_watch.evaluate(asset, [row()], T0, settings).verdict == PASS


def test_near_unlock_blocks_even_if_another_row_says_none(asset, settings):
    rows = [row(), row(status="scheduled", next_unlock_utc=iso(T0 + 2 * DAY_MS))]
    assert unlock_watch.evaluate(asset, rows, T0, settings).verdict == REJECT
