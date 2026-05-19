"""
test_scheduler.py — tests for the internal APScheduler integration.

Run with:  .venv/bin/python -m pytest test_scheduler.py -v
Requires no live MongoDB or SMTP — everything is mocked.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: load app.py with Mongo, APScheduler, and the reloader function mocked
# ---------------------------------------------------------------------------

def _load_app(extra_env=None, reloader_active=True):
    """
    Import (or re-import) app.py with:
    - MongoClient mocked (no live Mongo needed)
    - werkzeug.serving.is_running_from_reloader patched to reloader_active
      (True = suppress scheduler start; False = let it start)
    - APScheduler mocked when reloader_active is False so no real threads spawn
    Returns the imported module.
    """
    # Drop any previously imported copy so we always get a fresh module.
    sys.modules.pop("app", None)

    env = {
        "SECRET_KEY": "test-secret",
        "MONGO_URI": "mongodb://localhost:27017",
        "MONGO_DB": "test_db",
        "ADMIN_PIN": "1234",
        "SMTP_HOST": "",
        "ADMIN_EMAIL": "",
        "CRON_TOKEN": "test-token",
    }
    if extra_env:
        env.update(extra_env)

    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_col.create_index = MagicMock()

    # Build a mock BackgroundScheduler so no background threads start
    mock_scheduler_instance = MagicMock()
    mock_scheduler_cls = MagicMock(return_value=mock_scheduler_instance)

    # Patch werkzeug.serving.is_running_from_reloader (real werkzeug stays intact)
    with patch.dict("os.environ", env, clear=False), \
         patch("pymongo.MongoClient", return_value=mock_client), \
         patch("werkzeug.serving.is_running_from_reloader", return_value=reloader_active), \
         patch.dict("sys.modules", {
             "apscheduler": types.ModuleType("apscheduler"),
             "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
             "apscheduler.schedulers.background": _make_apscheduler_module(mock_scheduler_cls),
         }):
        import app as app_module

    app_module._mock_col = mock_col
    app_module._mock_db = mock_db
    app_module._mock_scheduler_cls = mock_scheduler_cls
    app_module._mock_scheduler = mock_scheduler_instance
    return app_module


def _make_apscheduler_module(scheduler_cls):
    mod = types.ModuleType("apscheduler.schedulers.background")
    mod.BackgroundScheduler = scheduler_cls
    return mod


# ---------------------------------------------------------------------------
# 1. Distributed lock — _run_job_with_lock
# ---------------------------------------------------------------------------

class TestRunJobWithLock(unittest.TestCase):

    def setUp(self):
        self.app = _load_app()

    def test_lock_acquired_runs_fn(self):
        """When insert_one succeeds (lock free), fn() must be called."""
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock()  # no exception → lock won

        fn = MagicMock()
        self.app._run_job_with_lock("test_job", fn)

        fn.assert_called_once()

    def test_lock_held_skips_fn(self):
        """When insert_one raises DuplicateKeyError, fn() must NOT be called."""
        from pymongo.errors import DuplicateKeyError

        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock(
            side_effect=DuplicateKeyError("dup")
        )

        fn = MagicMock()
        self.app._run_job_with_lock("test_job", fn)

        fn.assert_not_called()

    def test_lock_insert_stores_correct_fields(self):
        """insert_one should be called with _id=job_key, expires, pid."""
        import os
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock()

        before = datetime.utcnow()
        self.app._run_job_with_lock("myjob", MagicMock(), lock_ttl_seconds=100)
        after = datetime.utcnow()

        args = self.app.scheduler_locks_col.insert_one.call_args[0][0]
        self.assertEqual(args["_id"], "myjob")
        self.assertEqual(args["pid"], os.getpid())
        self.assertGreaterEqual(args["expires"], before + timedelta(seconds=99))
        self.assertLessEqual(args["expires"], after + timedelta(seconds=101))

    def test_fn_exception_does_not_propagate(self):
        """Exceptions in fn() must be swallowed (logged) and not crash the scheduler."""
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock()

        def _boom():
            raise RuntimeError("boom")

        # Should not raise
        self.app._run_job_with_lock("test_job", _boom)

    def test_insert_error_does_not_propagate(self):
        """Unexpected Mongo errors during lock acquisition must be swallowed."""
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock(
            side_effect=Exception("connection refused")
        )

        fn = MagicMock()
        # Should not raise
        self.app._run_job_with_lock("test_job", fn)
        fn.assert_not_called()


# ---------------------------------------------------------------------------
# 2. _scheduled_summary
# ---------------------------------------------------------------------------

class TestScheduledSummary(unittest.TestCase):

    def setUp(self):
        self.app = _load_app()

    def test_calls_send_summary_when_lock_free(self):
        """_scheduled_summary must invoke send_summary_email_if_due when lock is free."""
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock()

        with patch.object(self.app, "send_summary_email_if_due") as mock_fn:
            self.app._scheduled_summary()
            mock_fn.assert_called_once()
            # called with a datetime
            args, _ = mock_fn.call_args
            self.assertIsInstance(args[0], datetime)

    def test_skips_send_summary_when_lock_held(self):
        from pymongo.errors import DuplicateKeyError
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock(
            side_effect=DuplicateKeyError("dup")
        )

        with patch.object(self.app, "send_summary_email_if_due") as mock_fn:
            self.app._scheduled_summary()
            mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# 3. _scheduled_replenish
# ---------------------------------------------------------------------------

class TestScheduledReplenish(unittest.TestCase):

    def setUp(self):
        self.app = _load_app()

    def _free_lock(self):
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock()

    def test_replenish_due_items_are_applied(self):
        self._free_lock()

        fake_item = {
            "_id": "abc",
            "name": "Widget",
            "stock": 2,
            "low_stock_threshold": 5,
            "unit": "pcs",
            "auto_replenish_enabled": True,
            "auto_replenish_qty": 10,
            "auto_replenish_max_stock": None,
            "auto_replenish_interval_type": "days",
            "auto_replenish_interval_value": 1,
            "last_replenished_utc": datetime.utcnow() - timedelta(days=2),
        }
        self.app.items_col = MagicMock()
        self.app.items_col.find = MagicMock(return_value=[fake_item])

        with patch.object(self.app, "_ensure_item_defaults", side_effect=lambda x: x), \
             patch.object(self.app, "_is_replenish_due", return_value=True), \
             patch.object(self.app, "_apply_replenish") as mock_apply:
            self.app._scheduled_replenish()

        mock_apply.assert_called_once_with("Widget", 10, None)

    def test_non_due_items_skipped(self):
        self._free_lock()

        fake_item = {"name": "Gadget", "auto_replenish_enabled": True}
        self.app.items_col = MagicMock()
        self.app.items_col.find = MagicMock(return_value=[fake_item])

        with patch.object(self.app, "_ensure_item_defaults", side_effect=lambda x: x), \
             patch.object(self.app, "_is_replenish_due", return_value=False), \
             patch.object(self.app, "_apply_replenish") as mock_apply:
            self.app._scheduled_replenish()

        mock_apply.assert_not_called()

    def test_replenish_apply_exception_does_not_stop_loop(self):
        """An exception in _apply_replenish for one item must not abort the others."""
        self._free_lock()

        items = [
            {"name": "A", "auto_replenish_enabled": True,
             "auto_replenish_qty": 5, "auto_replenish_max_stock": None},
            {"name": "B", "auto_replenish_enabled": True,
             "auto_replenish_qty": 3, "auto_replenish_max_stock": None},
        ]
        self.app.items_col = MagicMock()
        self.app.items_col.find = MagicMock(return_value=items)

        call_count = []

        def _apply(name, qty, cap):
            call_count.append(name)
            if name == "A":
                raise RuntimeError("db error")

        with patch.object(self.app, "_ensure_item_defaults", side_effect=lambda x: x), \
             patch.object(self.app, "_is_replenish_due", return_value=True), \
             patch.object(self.app, "_apply_replenish", side_effect=_apply):
            self.app._scheduled_replenish()

        self.assertIn("A", call_count)
        self.assertIn("B", call_count)

    def test_skips_all_when_lock_held(self):
        from pymongo.errors import DuplicateKeyError
        self.app.scheduler_locks_col = MagicMock()
        self.app.scheduler_locks_col.insert_one = MagicMock(
            side_effect=DuplicateKeyError("dup")
        )
        self.app.items_col = MagicMock()
        self.app.items_col.find = MagicMock()

        with patch.object(self.app, "_apply_replenish") as mock_apply:
            self.app._scheduled_replenish()

        self.app.items_col.find.assert_not_called()
        mock_apply.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Scheduler startup guard — only starts when NOT in reloader
# ---------------------------------------------------------------------------

class TestSchedulerStartupGuard(unittest.TestCase):

    def test_scheduler_not_started_inside_reloader(self):
        m = _load_app(reloader_active=True)
        m._mock_scheduler_cls.assert_not_called()
        m._mock_scheduler.start.assert_not_called()

    def test_scheduler_started_outside_reloader(self):
        m = _load_app(reloader_active=False)
        m._mock_scheduler_cls.assert_called_once()
        m._mock_scheduler.start.assert_called_once()

    def test_scheduler_has_two_jobs(self):
        m = _load_app(reloader_active=False)
        self.assertEqual(m._mock_scheduler.add_job.call_count, 2)
        ids = [c.kwargs["id"] for c in m._mock_scheduler.add_job.call_args_list]
        self.assertIn("summary", ids)
        self.assertIn("replenish", ids)

    def test_scheduler_jobs_use_interval_trigger(self):
        m = _load_app(reloader_active=False)
        for c in m._mock_scheduler.add_job.call_args_list:
            trigger = c.args[1] if len(c.args) > 1 else c.kwargs.get("trigger")
            self.assertEqual(trigger, "interval")

    def test_scheduler_daemon_and_utc(self):
        m = _load_app(reloader_active=False)
        init_kwargs = m._mock_scheduler_cls.call_args.kwargs
        self.assertTrue(init_kwargs.get("daemon"))
        self.assertEqual(str(init_kwargs.get("timezone")), "UTC")


# ---------------------------------------------------------------------------
# 5. HTTP task endpoints still work (belt-and-suspenders external cron)
# ---------------------------------------------------------------------------

class TestTaskEndpoints(unittest.TestCase):

    def setUp(self):
        self.app_module = _load_app(extra_env={"CRON_TOKEN": "secret-tok"})
        self.client = self.app_module.app.test_client()

    def test_summary_endpoint_unauthorized_no_token(self):
        r = self.client.get("/tasks/summary")
        self.assertEqual(r.status_code, 401)

    def test_summary_endpoint_unauthorized_wrong_token(self):
        r = self.client.get("/tasks/summary?token=wrong")
        self.assertEqual(r.status_code, 401)

    def test_summary_endpoint_authorized(self):
        with patch.object(self.app_module, "send_summary_email_if_due", return_value=False):
            r = self.client.get("/tasks/summary?token=secret-tok")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])

    def test_replenish_endpoint_unauthorized(self):
        r = self.client.get("/tasks/replenish?token=bad")
        self.assertEqual(r.status_code, 401)

    def test_replenish_endpoint_authorized_no_items(self):
        self.app_module.items_col = MagicMock()
        self.app_module.items_col.find = MagicMock(return_value=[])
        r = self.client.get("/tasks/replenish?token=secret-tok")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["replenished"], [])

    def test_health_endpoint(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
