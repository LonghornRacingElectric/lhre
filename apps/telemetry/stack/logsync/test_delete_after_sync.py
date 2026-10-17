"""Tests for delete-after-sync: app/pi.py delete_verified, the worker hook in
app/manager.py, app/config.py _bool.

No SSH and no car: the "remote" commands run in a local shell against a temp
directory that stands in for the Pi's log dir.

Run from this directory with `python -m unittest test_delete_after_sync`.
"""
import asyncio
import dataclasses
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    import asyncpg  # noqa: F401
except ImportError:   # the job manager imports it; nothing here touches the DB
    sys.modules["asyncpg"] = mock.MagicMock()

from app import config as config_mod
from app import manager, pi
from app.models import JobState, RemoteFile

HOUR = 3600


class DeleteVerifiedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A space and a quote in the path: the exact-path quoting has to hold.
        self.car = os.path.join(tmp.name, "car's logs")
        self.server = os.path.join(tmp.name, "server")
        os.mkdir(self.car)
        os.mkdir(self.server)
        self.outside = os.path.join(tmp.name, "orion_9.csv")   # not in the log dir
        with open(self.outside, "w") as fh:
            fh.write("keep")
        # The log being written: newest by name and by mtime.
        self.put("orion_9000.csv", age_s=1)

    def put(self, name, car="data", server="data", age_s=2 * HOUR):
        """A log on the "car" (None = absent) and its server copy (None = absent)."""
        if car is not None:
            path = os.path.join(self.car, name)
            with open(path, "w") as fh:
                fh.write(car)
            t = time.time() - age_s
            os.utime(path, (t, t))
        if server is not None:
            with open(os.path.join(self.server, name), "w") as fh:
                fh.write(server)

    def on_car(self, name):
        return os.path.exists(os.path.join(self.car, name))

    def run_pass(self, names, dry_run=False):
        cfg = dataclasses.replace(
            config_mod.config, remote_log_dir=self.car,
            delete_dry_run=dry_run, delete_min_age_s=300,
        )
        local = {n: os.path.join(self.server, n) for n in names}
        with mock.patch.object(pi, "config", cfg), \
                mock.patch.object(pi, "_ssh_base_cmd", return_value=["sh", "-c"]):
            out = asyncio.run(pi.delete_verified(local))
        self.assertEqual([d["name"] for d in out], list(names))
        return {d["name"]: d for d in out}

    def test_deletes_only_verified_files(self):
        self.put("orion_1000.csv")
        self.put("orion_2000.csv", car="datA")                 # same size, other bytes
        self.put("orion_3000.csv", car="data grew")            # still growing on the car
        self.put("orion_4000.csv", server=None)                # never copied
        self.put("orion_5000.csv", age_s=10)                   # written 10 s ago
        self.put("orion_6000.csv", car=None)                   # not on the car
        names = ["orion_1000.csv", "orion_2000.csv", "orion_3000.csv", "orion_4000.csv",
                 "orion_5000.csv", "orion_6000.csv", "orion_9000.csv", "../orion_9.csv", "*"]
        out = self.run_pass(names)

        self.assertEqual(out["orion_1000.csv"]["action"], "deleted")
        self.assertFalse(self.on_car("orion_1000.csv"))
        for name, why in [
            ("orion_2000.csv", "sha256 differs"), ("orion_3000.csv", "size differs"),
            ("orion_4000.csv", "no server copy"), ("orion_5000.csv", "modified"),
            ("orion_6000.csv", "not on the car"), ("orion_9000.csv", "newest"),
            ("../orion_9.csv", "not on the car"), ("*", "not on the car"),
        ]:
            self.assertEqual(out[name]["action"], "skipped", name)
            self.assertIn(why, out[name]["reason"])
        self.assertEqual(sorted(os.listdir(self.car)), [
            "orion_2000.csv", "orion_3000.csv", "orion_4000.csv",
            "orion_5000.csv", "orion_9000.csv"])
        self.assertTrue(os.path.exists(self.outside))

    def test_dry_run_deletes_nothing(self):
        self.put("orion_1000.csv")
        self.put("orion_2000.csv", car="datA")
        out = self.run_pass(["orion_1000.csv", "orion_2000.csv"], dry_run=True)
        self.assertEqual(out["orion_1000.csv"]["action"], "would_delete")
        self.assertEqual(out["orion_2000.csv"]["action"], "skipped")
        self.assertTrue(self.on_car("orion_1000.csv"))

    def test_newest_by_mtime_is_kept_when_names_are_out_of_order(self):
        # Stale Pi clock at boot: the log being written has an older name.
        self.put("orion_9000.csv", age_s=HOUR)
        self.put("orion_1000.csv", age_s=400)
        out = self.run_pass(["orion_1000.csv"])
        self.assertEqual(out["orion_1000.csv"]["action"], "skipped")
        self.assertIn("newest", out["orion_1000.csv"]["reason"])
        self.assertTrue(self.on_car("orion_1000.csv"))

    @unittest.skipIf(os.geteuid() == 0, "root can remove from a read-only directory")
    def test_failed_delete_is_reported(self):
        self.put("orion_1000.csv")
        os.chmod(self.car, 0o555)
        self.addCleanup(os.chmod, self.car, 0o755)
        out = self.run_pass(["orion_1000.csv"])
        self.assertEqual(out["orion_1000.csv"]["action"], "failed")
        self.assertIn("not confirmed by the car", out["orion_1000.csv"]["reason"])
        self.assertIn("orion_1000.csv", out["orion_1000.csv"]["reason"])   # rm's own message
        self.assertTrue(self.on_car("orion_1000.csv"))


class WorkerHookTest(unittest.TestCase):
    """The worker runs the delete step for a job that just completed, and only then."""

    def finish_job(self, end_state, enabled=True, delete=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = dataclasses.replace(
            config_mod.config, staging_dir=tmp.name,
            state_db=os.path.join(tmp.name, "state", "logsync.db"), delete_after_sync=enabled,
        )
        delete = delete or mock.AsyncMock(
            return_value=[{"name": "orion_1.csv", "action": "deleted", "reason": "match"}])

        async def go():
            mgr = manager.JobManager()
            job = await mgr.create_job(0, 1, [RemoteFile("orion_1.csv", 1, 2, 4)], None)
            ids = [job.id]

            async def run_job(job, ctrl):   # stands in for the rsync loop
                job.state = end_state

            async def get():                # hand out the one job, then stop the worker
                if ids:
                    return ids.pop()
                raise asyncio.CancelledError
            mgr._run_job, mgr._queue.get = run_job, get
            with self.assertRaises(asyncio.CancelledError):
                await mgr._worker_loop()
            return mgr.store.get(job.id)    # the job as persisted

        with mock.patch.object(manager, "config", cfg), \
                mock.patch.object(manager, "delete_verified", delete):
            return asyncio.run(go()), delete

    def test_runs_after_a_completed_job(self):
        job, delete = self.finish_job(JobState.COMPLETED)
        delete.assert_awaited_once()
        self.assertEqual(list(delete.await_args.args[0]), ["orion_1.csv"])
        self.assertEqual(job.deletions[0]["action"], "deleted")
        self.assertIsNone(job.error)

    def test_not_for_unfinished_jobs_or_when_off(self):
        for state in (JobState.FAILED, JobState.CANCELED, JobState.PAUSED, JobState.PAUSED_MOTION):
            self.finish_job(state)[1].assert_not_awaited()
        self.finish_job(JobState.COMPLETED, enabled=False)[1].assert_not_awaited()

    def test_a_failed_delete_leaves_the_job_completed(self):
        for delete in (
            mock.AsyncMock(side_effect=RuntimeError("car unreachable")),
            mock.AsyncMock(return_value=[
                {"name": "orion_1.csv", "action": "failed", "reason": "rm: denied"}]),
        ):
            job, _ = self.finish_job(JobState.COMPLETED, delete=delete)
            self.assertEqual(job.state, JobState.COMPLETED)
            self.assertIn("sync complete, but", job.error)


class BoolEnvTest(unittest.TestCase):
    def test_bool(self):
        for raw, want in [("true", True), (" On ", True), ("1", True),
                          ("false", False), ("0", False), ("", False)]:
            with mock.patch.dict(os.environ, {"X_FLAG": raw}):
                self.assertIs(config_mod._bool("X_FLAG", False), want, raw)
        with mock.patch.dict(os.environ, {"X_FLAG": "ture"}):   # a typo must not arm it
            with self.assertRaises(ValueError):
                config_mod._bool("X_FLAG", False)

    def test_defaults_are_off_and_dry_run(self):
        self.assertFalse(config_mod.Config.delete_after_sync)
        self.assertTrue(config_mod.Config.delete_dry_run)


if __name__ == "__main__":
    unittest.main()
