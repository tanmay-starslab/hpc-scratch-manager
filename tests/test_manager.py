import contextlib
import errno
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hpc_scratch_manager.cli import main
from hpc_scratch_manager.config import load
from hpc_scratch_manager.engine import db_open, run
from hpc_scratch_manager.probe import probe
from hpc_scratch_manager.safe_fs import open_root, refresh_one, secure_open
from hpc_scratch_manager.scheduler import job_name, reset_retries, trigger
from hpc_scratch_manager.state import atomic_json, due_at, locked, status


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "scratch"
        self.root.mkdir()
        self.state = self.base / "private"
        self.config = self.base / "settings.json"
        self.raw = {"roots": [str(self.root)], "state_dir": str(self.state),
                    "operations_per_second": 100000, "max_seconds": 10}
        self.save()

    def save(self, **kwargs):
        self.raw.update(kwargs)
        self.config.write_text(json.dumps(self.raw))
        self.config.chmod(0o600)
        self.c = load(self.config)

    def enable(self, method="read-byte", **kwargs):
        self.save(refresh_enabled=True, site_approval="synthetic test approval", behavior_confirmed=True,
                  method=method, **kwargs)

    def file(self, name="data", age=70, data=b"synthetic\n"):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        before = p.stat()
        os.utime(p, ns=(time.time_ns() - int(age * 86400e9), before.st_mtime_ns))
        return p

    def inventory_row(self, p):
        st = p.stat()
        return {"dev": st.st_dev, "ino": st.st_ino, "mtime": st.st_mtime_ns,
                "ctime": st.st_ctime_ns, "size": st.st_size}


class InventoryTests(Fixture):
    def test_audit_metadata_only_unusual_filenames(self):
        names = ["space name", "line\nbreak", "-option", "quotes'\"", "unicode-λ", os.fsdecode(b"raw-\xff")]
        files = [self.file(n) for n in names]
        stamps = [(p.stat().st_atime_ns, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in files]
        with patch("hpc_scratch_manager.engine.refresh_one", side_effect=AssertionError("content operation")):
            report, code = run(self.c, "audit")
        self.assertEqual(code, 0)
        self.assertEqual(report["files"], len(names))
        self.assertEqual(report["approaching_threshold"], len(names))
        self.assertEqual(report["access_age_buckets"]["60_to_90_days"], len(names))
        self.assertEqual(report["bytes"], len(b"synthetic\n") * len(names))
        self.assertEqual(stamps, [(p.stat().st_atime_ns, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in files])
        entries = [json.loads(s) for s in (self.state / "audit-report.jsonl").read_text().splitlines()]
        self.assertEqual({e["relative_path"] for e in entries if e["kind"] == "file"}, set(names))
        self.assertTrue(all(e["purge_date_is_estimate"] for e in entries if e["kind"] == "file"))

    def test_exclusions_symlinks_fifo_socket(self):
        import socket
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret").write_bytes(b"do not read")
        self.file("keep")
        self.file("excluded/hidden")
        (self.root / "alias").symlink_to(outside, target_is_directory=True)
        (self.root / "link").symlink_to(outside / "secret")
        os.mkfifo(self.root / "fifo")
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(str(self.root / "socket"))
        self.save(exclude=["excluded"])
        result, code = run(self.c, "audit")
        self.assertEqual(code, 0)
        self.assertEqual(result["files"], 1)
        self.assertGreaterEqual(result["outcomes"]["skipped"], 5)

    def test_symlink_root_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.save(roots=[str(alias)])
        with self.assertRaises(OSError):
            run(self.c, "audit")

    def test_kernel_boundary_rejects_dotdot_and_mount(self):
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        with self.assertRaises(OSError):
            secure_open(fd, b"../settings.json", os.O_PATH)
        # /proc is a real separate mount: no root privileges or synthetic mount needed.
        top = open_root("/")
        self.addCleanup(os.close, top)
        with self.assertRaises(OSError) as cm:
            secure_open(top, b"proc/version", os.O_PATH)
        self.assertEqual(cm.exception.errno, errno.EXDEV)

    def test_injected_cross_device_subtree_not_scanned(self):
        self.file("mounted/hidden")
        from hpc_scratch_manager import engine
        real = engine.secure_open
        def denied(fd, rel, flags):
            if rel == b"mounted":
                raise OSError(errno.EXDEV, "synthetic mount boundary")
            return real(fd, rel, flags)
        with patch.object(engine, "secure_open", side_effect=denied):
            result, code = run(self.c, "audit")
        self.assertEqual(result["files"], 0)
        self.assertEqual(code, 0)

    def test_unreadable_and_disappearing_are_partial(self):
        self.file("sub/data")
        from hpc_scratch_manager import engine
        real = engine.secure_open
        def denied(fd, rel, flags):
            if rel == b"sub":
                raise PermissionError(errno.EACCES, "synthetic unreadable")
            if rel == b"gone":
                raise FileNotFoundError(errno.ENOENT, "synthetic disappearance")
            return real(fd, rel, flags)
        self.file("gone")
        with patch.object(engine, "secure_open", side_effect=denied):
            result, code = run(self.c, "audit")
        self.assertEqual(code, 2)
        self.assertEqual(result["outcomes"]["failed"], 2)
        errors = [json.loads(line) for line in (self.state / "audit-report.jsonl").read_text().splitlines()]
        self.assertTrue(any(r["relative_path"] == "gone" and r["outcome"] == "failed" for r in errors))
        result, code = run(self.c, "audit", resume=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["files"], 2)

    def test_limit_and_checkpoint_recovery_without_double_counts(self):
        for i in range(8):
            self.file(f"data{i}")
        self.save(max_entries=3)
        codes = []
        for i in range(5):
            result, code = run(self.c, "audit", resume=i > 0)
            codes.append(code)
            if code == 0:
                break
        self.assertEqual(codes[0], 2)
        self.assertEqual(codes[-1], 0)
        self.assertEqual(result["files"], 8)
        with self.assertRaises(ValueError):
            run(self.c, "audit", resume=True)

    def test_interruption_atomic_recovery(self):
        for i in range(5):
            self.file(str(i))
        from hpc_scratch_manager import engine
        real = engine.Budget.tick
        calls = []
        def stop(b):
            calls.append(1)
            if len(calls) == 3:
                raise KeyboardInterrupt()
            real(b)
        with patch.object(engine.Budget, "tick", stop), self.assertRaises(KeyboardInterrupt):
            run(self.c, "audit")
        self.assertEqual(status(self.c)["runs"]["audit"]["outcome"], "interrupted")
        result, code = run(self.c, "audit", resume=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["files"], 5)

    def test_root_replaced_checkpoint_rejected(self):
        self.file("one")
        self.file("two")
        self.save(max_entries=1)
        run(self.c, "audit")
        self.root.rename(self.base / "old")
        self.root.mkdir()
        with self.assertRaisesRegex(ValueError, "root identity"):
            run(self.c, "audit", resume=True)

    def test_config_boundaries_and_private_settings(self):
        for change in ({"roots": [str(self.root), str(self.root / "nested")]},
                       {"state_dir": str(self.root / "state")}, {"workers": 100},
                       {"method": "rewrite"}, {"typo": True}, {"interval_days": float("nan")}):
            self.config.write_text(json.dumps(self.raw | change))
            with self.assertRaises(ValueError):
                load(self.config)
        self.save()
        self.config.chmod(0o644)
        with self.assertRaises(ValueError):
            load(self.config)


class RefreshTests(Fixture):
    def test_default_dry_run_and_execution_gate(self):
        p = self.file()
        st = p.stat()
        result, code = run(self.c, "refresh")
        self.assertEqual(code, 0)
        self.assertEqual(st.st_atime_ns, p.stat().st_atime_ns)
        self.assertIsNone(status(self.c)["last_successful_completion"])
        with self.assertRaises(ValueError):
            run(self.c, "refresh", execute=True)

    def test_read_byte_verified_contents_and_mtime_preserved(self):
        p = self.file(data=b"synthetic payload" * 1000)
        old = p.stat()
        self.enable()
        result, code = run(self.c, "refresh", execute=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["outcomes"]["verified"], 1)
        self.assertEqual(p.stat().st_mtime_ns, old.st_mtime_ns)
        self.assertEqual(p.stat().st_ctime_ns, old.st_ctime_ns)
        self.assertEqual(p.read_bytes(), b"synthetic payload" * 1000)
        self.assertFalse(status(self.c)["due"])

    def test_utime_empty_and_nonempty_mtime_preserved(self):
        files = [self.file("empty", data=b""), self.file("normal")]
        mtimes = [p.stat().st_mtime_ns for p in files]
        self.enable("utime")
        result, code = run(self.c, "refresh", execute=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["outcomes"]["verified"], 2)
        self.assertEqual(mtimes, [p.stat().st_mtime_ns for p in files])
        self.assertEqual(files[0].read_bytes(), b"")

    def test_empty_read_unverified_no_success(self):
        self.file(data=b"")
        self.enable()
        result, code = run(self.c, "refresh", execute=True)
        self.assertEqual(code, 2)
        self.assertEqual(result["outcomes"]["unverified"], 1)
        self.assertIsNone(status(self.c)["last_successful_completion"])

    def test_noatime_simulation_no_fallback(self):
        p = self.file()
        before = p.stat()
        row = self.inventory_row(p)
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        self.enable()
        with patch("hpc_scratch_manager.safe_fs.os.read", return_value=b"x"), patch("hpc_scratch_manager.safe_fs.os.utime", side_effect=AssertionError("fallback")):
            result = refresh_one(fd, b"data", row, self.c)
        self.assertEqual(result["outcome"], "unverified")
        self.assertEqual(before.st_mtime_ns, p.stat().st_mtime_ns)

    def test_race_symlink_file_parent_and_device_substitution(self):
        outside = self.base / "outside"
        outside.mkdir()
        target = outside / "research"
        target.write_bytes(b"untouched")
        before = target.stat()
        p = self.file("dir/data")
        row = self.inventory_row(p)
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        self.enable("utime")
        p.unlink()
        p.symlink_to(target)
        self.assertEqual(refresh_one(fd, b"dir/data", row, self.c)["outcome"], "failed")
        p.unlink()
        os.mkfifo(p)
        self.assertEqual(refresh_one(fd, b"dir/data", row, self.c)["outcome"], "failed")
        p.unlink()
        p.parent.rmdir()
        p.parent.symlink_to(outside, target_is_directory=True)
        self.assertEqual(refresh_one(fd, b"dir/research", row, self.c)["outcome"], "failed")
        self.assertEqual(before.st_atime_ns, target.stat().st_atime_ns)
        self.assertEqual(before.st_mtime_ns, target.stat().st_mtime_ns)
        self.assertEqual(before.st_ctime_ns, target.stat().st_ctime_ns)

    def test_hardlinks_rejected_and_changed_file_rejected(self):
        p = self.file()
        row = self.inventory_row(p)
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        self.enable("utime")
        link = self.base / "alias"
        os.link(p, link)
        self.assertIn("hard-linked", refresh_one(fd, b"data", row, self.c)["reason"])
        link.unlink()
        p.write_bytes(b"new bytes")
        self.assertIn("changed", refresh_one(fd, b"data", row, self.c)["reason"])

    def test_bounded_concurrency_and_partial_resume_skips_verified(self):
        import threading
        for i in range(7):
            self.file(str(i))
        self.enable(workers=2)
        active, maximum, seen = 0, 0, []
        mutex = threading.Lock()
        def fake(fd, rel, row, c):
            nonlocal active, maximum
            with mutex:
                active += 1
                maximum = max(maximum, active)
                seen.append(rel)
            time.sleep(0.005)
            with mutex:
                active -= 1
            return {"outcome": "unverified" if rel == b"3" else "verified", "reason": "synthetic"}
        with patch("hpc_scratch_manager.engine.refresh_one", side_effect=fake):
            report, code = run(self.c, "refresh", execute=True)
        self.assertEqual(code, 2)
        self.assertLessEqual(maximum, 2)
        seen.clear()
        def ok(fd, rel, row, c):
            seen.append(rel)
            return {"outcome": "verified", "reason": "synthetic"}
        with patch("hpc_scratch_manager.engine.refresh_one", side_effect=ok):
            report, code = run(self.c, "refresh", execute=True, resume=True)
        self.assertEqual(code, 0)
        self.assertEqual(seen, [b"3"])

    def test_lock_prevents_overlap(self):
        self.file()
        run(self.c, "audit")
        with locked(self.state / "work.lock"), self.assertRaisesRegex(RuntimeError, "lock"):
            run(self.c, "audit")

    def test_owner_filter(self):
        # Synthetic metadata identity, never chown real files or require root.
        p = self.file()
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        self.enable("utime")
        with patch("hpc_scratch_manager.safe_fs.os.getuid", return_value=os.getuid() + 100):
            self.assertEqual(refresh_one(fd, b"data", self.inventory_row(p), self.c)["outcome"], "failed")


    def test_export_failure_does_not_record_success_and_can_resume(self):
        self.file()
        self.enable()
        with patch("hpc_scratch_manager.engine.export", side_effect=OSError(errno.ENOSPC, "synthetic full disk")):
            with self.assertRaises(OSError):
                run(self.c, "refresh", execute=True)
        self.assertIsNone(status(self.c)["last_successful_completion"])
        # Verified filesystem work is committed even though report generation failed.
        with patch("hpc_scratch_manager.engine.refresh_one", side_effect=AssertionError("duplicate access")):
            result, code = run(self.c, "refresh", execute=True, resume=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["outcomes"]["verified"], 1)

    def test_atomic_state_failure_preserves_previous_checkpoint(self):
        self.state.mkdir(mode=0o700)
        path = self.state / "atomic.json"
        atomic_json(path, {"previous": True})
        with patch("hpc_scratch_manager.state.os.replace", side_effect=OSError(errno.EIO, "synthetic failure")):
            with self.assertRaises(OSError):
                atomic_json(path, {"new": True})
        self.assertEqual(json.loads(path.read_text()), {"previous": True})
        self.assertFalse(list(self.state.glob(".checkpoint-*")))

    def test_utime_ctime_effect_reported(self):
        p = self.file(data=b"")
        row = self.inventory_row(p)
        fd = open_root(str(self.root))
        self.addCleanup(os.close, fd)
        self.enable("utime")
        result = refresh_one(fd, b"data", row, self.c)
        self.assertEqual(result["outcome"], "verified")
        self.assertTrue(result["mtime_preserved"])
        self.assertEqual(result["ctime_changed"], p.stat().st_ctime_ns != row["ctime"])


class SchedulerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.enable(scheduler_enabled=True, scheduler_approval="synthetic scheduler approval",
                    job_script="/persistent/example/job.sh", sbatch_args=["--partition=CONFIRMED"])
        self.calls = []
        self.queue = ""
        def fake(args):
            self.calls.append(args)
            class Result:
                stdout = self.queue if args[0] == self.c["squeue"] else "12345\n"
            return Result()
        self.mock = patch("hpc_scratch_manager.scheduler.command", side_effect=fake)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_elapsed_interval_exactly_60_days(self):
        state = {"runs": {"refresh": {"last_success": 1000, "fingerprint": self.c["fingerprint"]}}}
        self.assertEqual(due_at(state, self.c), 1000 + 60 * 86400)
        state["runs"]["refresh"]["fingerprint"] = "changed"
        self.assertEqual(due_at(state, self.c), 0)

    def test_submit_duplicate_backoff_and_retry_exhaustion(self):
        result = trigger(self.c)
        self.assertEqual(result["action"], "submitted")
        self.assertEqual(trigger(self.c)["action"], "retry-backoff")
        self.queue = "12345\n"
        self.assertEqual(trigger(self.c)["action"], "already-queued-or-running")
        submit_calls = [a for a in self.calls if a[0] == self.c["sbatch"]]
        self.assertEqual(len(submit_calls), 1)
        self.queue = ""
        record = json.loads((self.state / "submission.json").read_text())
        record.update(attempts=3, submitted_at=1)
        atomic_json(self.state / "submission.json", record)
        with self.assertRaisesRegex(RuntimeError, "budget"):
            trigger(self.c)
        reset_retries(self.c)
        self.assertEqual(trigger(self.c)["action"], "submitted")

    def test_trigger_no_scratch_traversal_even_missing_roots(self):
        self.root.rmdir()
        with patch("hpc_scratch_manager.engine.inventory", side_effect=AssertionError("traversal")):
            self.assertEqual(trigger(self.c)["action"], "submitted")

    def test_success_state_not_submission_determines_due(self):
        self.state.mkdir(mode=0o700)
        atomic_json(self.state / "state.json", {"runs": {"refresh": {
            "last_success": time.time(), "fingerprint": self.c["fingerprint"]}}})
        self.assertEqual(trigger(self.c)["action"], "not-due")
        self.assertEqual(self.calls, [])

    def test_queue_failure_unknown_submission_and_crash_window(self):
        with patch("hpc_scratch_manager.scheduler.command", side_effect=TimeoutError("synthetic timeout")):
            with self.assertRaises(TimeoutError):
                trigger(self.c)
        self.assertFalse((self.state / "submission.json").exists())
        self.state.mkdir(exist_ok=True, mode=0o700)
        atomic_json(self.state / "submission.json", {"status": "submitting", "token": "synthetic", "attempts": 1})
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            trigger(self.c)
        self.queue = "12345\n"
        self.assertEqual(trigger(self.c)["action"], "already-queued-or-running")
        self.assertEqual(json.loads((self.state / "submission.json").read_text())["status"], "queued")

    def test_sbatch_failure_blocks_blind_retry(self):
        import subprocess
        def fail(args):
            if args[0] == self.c["squeue"]:
                return type("R", (), {"stdout": ""})()
            raise subprocess.TimeoutExpired(args[0], 30)
        with patch("hpc_scratch_manager.scheduler.command", side_effect=fail):
            with self.assertRaises(subprocess.TimeoutExpired):
                trigger(self.c)
        self.assertEqual(json.loads((self.state / "submission.json").read_text())["status"], "unknown")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            trigger(self.c)

    def test_token_revocation_and_trigger_lock(self):
        trigger(self.c)
        old = json.loads((self.state / "submission.json").read_text())["token"]
        reset_retries(self.c)
        with self.assertRaisesRegex(ValueError, "token"):
            run(self.c, "scheduled-run", execute=True, resume=None, token=old)
        with locked(self.state / "trigger.lock"), self.assertRaises(RuntimeError):
            trigger(self.c)

    def test_status_missing_partial_and_stale(self):
        view = status(self.c)
        self.assertTrue(view["due"])
        self.assertTrue(any("missing" in s for s in view["flags"]))
        self.state.mkdir(mode=0o700)
        atomic_json(self.state / "state.json", {"runs": {"refresh": {
            "last_attempt": 1, "fingerprint": self.c["fingerprint"], "outcome": "running"}}})
        self.assertTrue(any("stale" in s for s in status(self.c)["flags"]))


class IntegrationTests(Fixture):
    def test_actual_subprocess_submission_and_tokenized_wrapper(self):
        import subprocess
        import sys
        p = self.file()
        old_mtime = p.stat().st_mtime_ns
        args_path = self.base / "submitted-args.json"
        sbatch = self.base / "fake-sbatch"
        queue = self.base / "fake-squeue"
        sbatch.write_text(f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]))\nprint('12345')\n")
        queue.write_text(f"#!{sys.executable}\n")
        sbatch.chmod(0o700)
        queue.chmod(0o700)
        wrapper = self.base / "job.sh"
        template = (Path(__file__).resolve().parents[1] / "scripts" / "slurm-job.sh").read_text()
        wrapper.write_text(template.replace(
            "/home/REPLACE_WITH_USER/.local/opt/hpc-scratch-manager/.venv/bin/python", sys.executable))
        wrapper.chmod(0o700)
        self.enable(scheduler_enabled=True, scheduler_approval="synthetic test", sbatch=str(sbatch),
                    squeue=str(queue), job_script=str(wrapper))
        output = subprocess.run([sys.executable, "-m", "hpc_scratch_manager", "--config", str(self.config), "trigger"],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(output.stdout)["action"], "submitted")
        self.assertIsNone(status(self.c)["last_successful_completion"])
        actual = json.loads(args_path.read_text())
        self.assertIn("--export=NONE", actual)
        self.assertIn("--job-name=" + job_name(self.c), actual)
        # Fake scheduler captures arguments; explicitly launch the exact wrapper/config/token.
        job = subprocess.run(actual[-3:], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(job.stdout)["outcomes"]["verified"], 1)
        self.assertEqual(old_mtime, p.stat().st_mtime_ns)
        self.assertFalse(status(self.c)["due"])
        output = subprocess.run([sys.executable, "-m", "hpc_scratch_manager", "--config", str(self.config), "trigger"],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(output.stdout)["action"], "not-due")

    def test_scheduled_runs_resume_bounded_checkpoints(self):
        self.file("first")
        self.file("second")
        self.enable(max_entries=1)
        outcomes = []
        for i in range(10):
            result, code = run(self.c, "refresh", execute=True, resume=None)
            outcomes.append(code)
            if code == 0:
                break
        self.assertEqual(outcomes[0], 2)
        self.assertEqual(outcomes[-1], 0)
        self.assertEqual(result["outcomes"]["verified"], 2)


class ProbeAndCLITests(Fixture):
    def test_probe_only_disposable_and_records_all_timestamps(self):
        research = self.file()
        st = research.stat()
        result = probe(str(self.root), str(self.state))
        self.assertEqual(len(result["observations"]), 20)
        self.assertEqual(list(self.root.iterdir()), [research])
        self.assertEqual(research.stat().st_atime_ns, st.st_atime_ns)
        for row in result["observations"]:
            self.assertEqual(set(row["before"]), {"atime_ns", "mtime_ns", "ctime_ns"})

    def test_cli_default_dry_run_and_status(self):
        self.file()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.config), "refresh"]), 0)
            self.assertEqual(main(["--config", str(self.config), "status"]), 0)


if __name__ == "__main__":
    unittest.main()
