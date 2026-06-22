"""
test_dedup_cleanup.py — tests for the duplicate-submission, undo, and
log-cleanup features.

Run with:  .venv/bin/python -m pytest test_dedup_cleanup.py -v
Reuses the Mongo/APScheduler-mocking loader from test_scheduler.py, so it
needs no live MongoDB.
"""
import unittest
from datetime import datetime, timedelta

from test_scheduler import _load_app


def _log(user, item, qty, t, before, after):
    return {
        "_id": f"{user}-{item}-{qty}-{t.isoformat()}",
        "time": t,
        "user": user,
        "item": item,
        "qty": qty,
        "before": before,
        "after": after,
    }


class ClusterLogsTests(unittest.TestCase):
    def setUp(self):
        self.app = _load_app(reloader_active=True)

    def test_rapid_sequential_checkouts_collapse(self):
        """Five 20-qty checkouts within 60s collapse to one; 80 restored."""
        base = datetime(2026, 6, 22, 17, 35, 38)
        stock = 0
        logs = []
        for offset in (0, 5, 8, 9, 17):  # 17:35:38 .. 17:35:55, all <=60s gaps
            before = stock
            stock -= 20
            logs.append(_log("Janet", "Bottles", 20, base + timedelta(seconds=offset),
                             before, stock))

        clusters = self.app._cluster_logs(logs, window_seconds=60)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c["remove_count"], 4)          # keep first, remove 4
        self.assertEqual(c["restore"], 80)              # 4 * 20 restored
        self.assertEqual(c["direction"], "checkout")

    def test_entries_outside_window_are_not_collapsed(self):
        """Same user/item/qty but spaced > window apart → no removal."""
        base = datetime(2026, 6, 22, 12, 0, 0)
        logs = [
            _log("Janet", "Bottles", 20, base, 100, 80),
            _log("Janet", "Bottles", 20, base + timedelta(minutes=10), 80, 60),
        ]
        clusters = self.app._cluster_logs(logs, window_seconds=60)
        self.assertEqual(clusters, [])

    def test_different_qty_not_grouped(self):
        base = datetime(2026, 6, 22, 12, 0, 0)
        logs = [
            _log("Janet", "Bottles", 20, base, 100, 80),
            _log("Janet", "Bottles", 30, base + timedelta(seconds=5), 80, 50),
        ]
        self.assertEqual(self.app._cluster_logs(logs, 60), [])

    def test_checkout_and_dropoff_not_mixed(self):
        """A checkout and a dropoff of the same qty are different directions."""
        base = datetime(2026, 6, 22, 12, 0, 0)
        logs = [
            _log("Janet", "Bottles", 20, base, 100, 80),                 # checkout
            _log("Janet", "Bottles", 20, base + timedelta(seconds=5), 80, 100),  # dropoff
        ]
        self.assertEqual(self.app._cluster_logs(logs, 60), [])

    def test_dropoff_duplicates_restore_negative(self):
        """Duplicate dropoffs over-credited stock; restore reverses (negative)."""
        base = datetime(2026, 6, 22, 12, 0, 0)
        logs = [
            _log("Janet", "Bottles", 20, base, 0, 20),
            _log("Janet", "Bottles", 20, base + timedelta(seconds=3), 20, 40),
        ]
        clusters = self.app._cluster_logs(logs, 60)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["restore"], -20)   # undo the extra +20
        self.assertEqual(clusters[0]["direction"], "dropoff")


class SubmitTokenTests(unittest.TestCase):
    def setUp(self):
        self.app = _load_app(reloader_active=True)

    def test_first_token_use_allowed(self):
        col = self.app.submit_tokens_col
        col.insert_one = lambda doc: None                # no DuplicateKeyError
        self.assertTrue(self.app._consume_submit_token("abc"))

    def test_replayed_token_rejected(self):
        from pymongo.errors import DuplicateKeyError

        def _raise(doc):
            raise DuplicateKeyError("dup")

        self.app.submit_tokens_col.insert_one = _raise
        self.assertFalse(self.app._consume_submit_token("abc"))

    def test_empty_token_rejected(self):
        self.assertFalse(self.app._consume_submit_token(""))


if __name__ == "__main__":
    unittest.main()
