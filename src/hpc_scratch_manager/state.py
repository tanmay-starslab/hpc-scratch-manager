"""Private durable state, atomic replacement and advisory overlap locks."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

from .safe_fs import open_root


def private_dir(path):
    os.umask(0o077)
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = open_root(str(p))
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError("state/test output directory must be owned by you and mode 700")
    finally:
        os.close(fd)
    return p


@contextmanager
def locked(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another operation holds this lock") from None
        yield
    finally:
        os.close(fd)


def read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd) as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("state must be a JSON object")
    return data


def atomic_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True, ensure_ascii=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        d = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def due_at(state, c):
    run = state.get("runs", {}).get("refresh", {})
    if run.get("fingerprint") != c["fingerprint"] or not run.get("last_success"):
        return 0.0
    return run["last_success"] + c["interval_days"] * 86400


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def status(c):
    p = Path(c["state_dir"])
    state = read_json(p / "state.json")
    pending = read_json(p / "submission.json")
    now = time.time()
    due = due_at(state, c)
    run = state.get("runs", {}).get("refresh", {})
    flags = []
    if not state:
        flags.append("missing state; no successful refresh recorded")
    if run and run.get("fingerprint") != c["fingerprint"]:
        flags.append("configuration changed; previous refresh does not cover this configuration")
    if due <= now:
        flags.append("refresh due or never successfully completed")
    if run.get("outcome") in ("partial", "interrupted", "running", "failed"):
        flags.append("partial/unfinished refresh; inspect report and checkpoint")
    if run.get("outcome") == "running" and now - run.get("last_attempt", 0) > c["stale_hours"] * 3600:
        flags.append("stale running state; check scheduler before recovery")
    if pending.get("status") in ("submitting", "unknown"):
        flags.append("submission outcome uncertain; automatic resubmission blocked")
    if pending.get("submitted_at") and now - pending["submitted_at"] > c["stale_hours"] * 3600:
        flags.append("old submission; status does not query Slurm; run trigger or check squeue")
    if pending.get("attempts", 0) >= c["max_attempts"]:
        flags.append("retry budget exhausted; investigate, then use reset-retries")
    return {"last_attempt": iso(run.get("last_attempt")), "last_successful_completion": iso(run.get("last_success")),
            "next_due_run": iso(due) or "now (no matching successful completion)",
            "due": due <= now, "refresh_enabled": c["refresh_enabled"], "flags": flags,
            "runs": state.get("runs", {}), "submission": pending,
            "note": "state-only view; does not access scratch or assert scheduler liveness"}
