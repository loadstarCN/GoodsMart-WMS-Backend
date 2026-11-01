from datetime import datetime, timedelta, timezone
from warehouse.inventory import pending


def test_unknown_age_is_not_assumed_overdue(monkeypatch):
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(pending, '_entries', lambda pairs: {(1, 1): [(now-timedelta(days=5), 2)]})
    monkeypatch.setattr(pending, '_tracked_entries', lambda pairs: {(1, 1): [(None, 3), (now-timedelta(days=4), 1)]})
    result = pending.overdue_summary([(1, 1, 10)], 3, now)
    assert result['overdue_quantity'] == 3
    assert result['unknown_age_quantity'] == 7
    assert result['overdue_inventory_count'] == 1
    assert result['oldest_pending_since'] is None


def test_fifo_overdue_preserves_recent_known_stock():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    assert pending._overdue_quantity([], 5, now) == 0
    assert pending._overdue_quantity([(now, 3), (now-timedelta(days=9), 7)], 5, now-timedelta(days=3)) == 2
