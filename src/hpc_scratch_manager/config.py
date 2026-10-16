"""Strict private JSON configuration; no cluster defaults are guessed."""
import hashlib
import json
import math
import os
from pathlib import Path

DEFAULTS = {
    "roots": [], "exclude": [], "interval_days": 60, "threshold_days": 90,
    "warning_days": 30, "owner_only": True, "workers": 1,
    "max_entries": 100000, "max_seconds": 1800, "max_depth": 64,
    "operations_per_second": 20, "refresh_enabled": False,
    "site_approval": "", "method": "read-byte", "behavior_confirmed": False,
    "freshness_tolerance_seconds": 5, "scheduler_enabled": False,
    "scheduler_approval": "", "sbatch": "/usr/bin/sbatch",
    "squeue": "/usr/bin/squeue", "sbatch_args": [], "job_script": "",
    "retry_hours": 24, "max_attempts": 3, "stale_hours": 48,
}


def absolute(value):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ValueError("paths must be absolute strings without NUL (expand placeholders first)")
    return os.path.normpath(value)


def load(path):
    path = Path(path)
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("configuration must be owned by you and mode 600")
    raw = json.loads(path.read_text())
    unknown = set(raw) - set(DEFAULTS) - {"state_dir"}
    if unknown:
        raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
    c = DEFAULTS | raw
    if not isinstance(c["roots"], list) or not c["roots"]:
        raise ValueError("roots must be a nonempty list")
    c["roots"] = [absolute(p) for p in c["roots"]]
    c["state_dir"] = absolute(c.get("state_dir"))
    for i, a in enumerate(c["roots"]):
        if a == "/":
            raise ValueError("filesystem root is not an acceptable scratch root")
        for b in c["roots"][i + 1:]:
            if os.path.commonpath([a, b]) in (a, b):
                raise ValueError("configured roots must not overlap")
        if os.path.commonpath([a, c["state_dir"]]) in (a, c["state_dir"]):
            raise ValueError("state_dir must be separate from scratch roots")
    if not isinstance(c["exclude"], list) or any(not isinstance(x, str) for x in c["exclude"]):
        raise ValueError("exclude must be a list of root-relative glob strings")
    for key in ("interval_days", "threshold_days", "max_seconds", "operations_per_second",
                "retry_hours", "stale_hours", "freshness_tolerance_seconds"):
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    if not isinstance(c["warning_days"], (int, float)) or not 0 <= c["warning_days"] <= c["threshold_days"]:
        raise ValueError("warning_days must be between 0 and threshold_days")
    for key, upper in (("workers", 8), ("max_depth", 128), ("max_entries", 10000000), ("max_attempts", 20)):
        if type(c[key]) is not int or not 1 <= c[key] <= upper:
            raise ValueError(f"{key} must be an integer from 1 to {upper}")
    for key in ("owner_only", "refresh_enabled", "behavior_confirmed", "scheduler_enabled"):
        if type(c[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key in ("site_approval", "scheduler_approval"):
        if not isinstance(c[key], str):
            raise ValueError(f"{key} must be a string")
    if c["method"] not in ("read-byte", "utime"):
        raise ValueError("method must be read-byte or utime")
    if not isinstance(c["sbatch_args"], list) or any(not isinstance(x, str) for x in c["sbatch_args"]):
        raise ValueError("sbatch_args must be an argument list, not a shell command")
    for key in ("sbatch", "squeue"):
        c[key] = absolute(c[key])
    if c["job_script"]:
        c["job_script"] = absolute(c["job_script"])
    # Changing any safety/selection/behavior setting invalidates prior due state and checkpoints.
    c["fingerprint"] = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()
    c["config_path"] = str(path.absolute())
    return c


def require_refresh(c):
    if not c["refresh_enabled"] or not c["site_approval"].strip() or not c["behavior_confirmed"]:
        raise ValueError("refresh requires refresh_enabled, site_approval and behavior_confirmed; see docs/policy.md")
