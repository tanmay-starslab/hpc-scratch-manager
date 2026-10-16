"""Daily state-only Slurm trigger with durable submission intent and bounded retries."""
import hashlib
import os
import re
import subprocess
import time
import uuid

from .config import require_refresh
from .state import atomic_json, due_at, locked, private_dir, read_json


def job_name(c):
    return "hpc-scratch-" + hashlib.sha256(c["state_dir"].encode()).hexdigest()[:12]


def command(args):
    return subprocess.run(args, text=True, capture_output=True, timeout=30, check=True)


def trigger(c):
    require_refresh(c)
    if not c["scheduler_enabled"] or not c["scheduler_approval"].strip() or not c["job_script"]:
        raise ValueError("scheduler requires scheduler_enabled, scheduler_approval and job_script")
    directory = private_dir(c["state_dir"])
    with locked(directory / "trigger.lock"):
        path = directory / "submission.json"
        record = read_json(path)
        state = read_json(directory / "state.json")
        now = time.time()
        if due_at(state, c) > now:
            return {"action": "not-due", "next_due_epoch": due_at(state, c)}
        # No scratch traversal or file metadata operations here. Fail closed on Slurm errors.
        queue = command([c["squeue"], "--noheader", "--user", str(os.getuid()), "--name", job_name(c),
                         "--states=all", "--format=%i"]).stdout
        active = [line.strip() for line in queue.splitlines() if line.strip()]
        if len(active) > 1:
            raise RuntimeError("multiple matching Slurm jobs; investigate before continuing")
        if active:
            if record.get("status") in ("submitting", "unknown") and record.get("token"):
                record.update(status="queued", job_id=active[0])
                atomic_json(path, record)
            return {"action": "already-queued-or-running", "job_id": active[0]}
        if record.get("status") in ("submitting", "unknown"):
            raise RuntimeError("uncertain submission with no visible job; reconcile squeue/sacct then reset-retries")
        if record.get("fingerprint") != c["fingerprint"]:
            if record.get("status") == "queued":
                raise RuntimeError("configuration changed with unresolved submission; reconcile then reset-retries")
            record = {}
        successful = state.get("runs", {}).get("refresh", {}).get("last_success", 0) or 0
        if successful > record.get("submitted_at", now):
            record = {}
        if record.get("attempts", 0) >= c["max_attempts"]:
            raise RuntimeError("retry budget exhausted; inspect failures then reset-retries")
        if now < record.get("submitted_at", 0) + c["retry_hours"] * 3600:
            return {"action": "retry-backoff"}
        # Never allow additional CLI options to override scheduler safety/name/output settings.
        allowed = ("--partition=", "--account=", "--qos=", "--time=", "--mem=", "--cpus-per-task=")
        if any(not a.startswith(allowed) or "\n" in a for a in c["sbatch_args"]):
            raise ValueError("sbatch_args supports only --partition/--account/--qos/--time/--mem/--cpus-per-task=value")
        record = {"status": "submitting", "token": uuid.uuid4().hex, "submitted_at": now,
                  "attempts": record.get("attempts", 0) + 1, "fingerprint": c["fingerprint"]}
        atomic_json(path, record)
        # An interrupted/timeout/failed sbatch is ambiguous: durable intent prevents blind retries.
        args = [c["sbatch"], "--parsable", *c["sbatch_args"], "--job-name=" + job_name(c),
                "--output=" + str(directory / "slurm-%j.log"), "--open-mode=append",
                "--export=NONE", c["job_script"], c["config_path"], record["token"]]
        try:
            out = command(args).stdout.strip()
            if not re.fullmatch(r"[0-9]+(?:;[A-Za-z0-9_.-]+)?", out):
                raise RuntimeError("unrecognized sbatch response; submission may have succeeded")
            if ";" in out:
                raise RuntimeError("federated cluster response unsupported; reconcile submission manually")
        except (OSError, subprocess.SubprocessError, RuntimeError):
            record["status"] = "unknown"
            atomic_json(path, record)
            raise
        record.update(status="queued", job_id=out)
        atomic_json(path, record)
        return {"action": "submitted", "job_id": out, "attempts": record["attempts"],
                "note": "submission is not successful refresh completion"}


def reset_retries(c):
    directory = private_dir(c["state_dir"])
    with locked(directory / "trigger.lock"), locked(directory / "work.lock"):
        # User explicitly runs this after reconciling queue/accounting. Token revocation
        # prevents an old delayed wrapper from operating, even after a mistaken reset.
        atomic_json(directory / "submission.json", {"status": "reset", "attempts": 0,
                    "reset_at": time.time(), "fingerprint": c["fingerprint"]})
    return {"action": "retry-budget-reset", "note": "queued jobs must be cancelled separately"}
