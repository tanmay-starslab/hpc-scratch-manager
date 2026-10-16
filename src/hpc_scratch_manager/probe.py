"""Disposable-only timestamp experiments; never operate on caller-named existing files."""
import os
from pathlib import Path
import tempfile
import time

from .safe_fs import open_root, refresh_one
from .state import atomic_json, private_dir


def stamps(path):
    st = path.stat()
    return {"atime_ns": st.st_atime_ns, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def probe(test_dir, output_dir):
    output = private_dir(output_dir)
    root = open_root(str(Path(test_dir).absolute()))
    os.close(root)
    # mkdtemp creates a unique mode-700 child; never clean up or modify preexisting data.
    with tempfile.TemporaryDirectory(prefix="hpc-scratch-probe-", dir=test_dir) as tmp:
        d = Path(tmp)
        rows = []
        for empty in (False, True):
            for operation in ("listing", "stat", "open", "read-byte", "utime"):
                p = d / (operation + ("-empty" if empty else "-data"))
                p.write_bytes(b"" if empty else b"synthetic disposable probe\n")
                # Two initial conditions: old atime and a recently accessed file.
                # Both are synthetic; backdating is never applied to research data.
                for age in (3 * 86400, 0):
                    st = p.stat()
                    os.utime(p, ns=(time.time_ns() - age * 10**9, st.st_mtime_ns))
                    before = stamps(p)
                    reason = None
                    if operation == "listing":
                        with os.scandir(d) as listing:
                            for _ in listing:
                                pass
                    elif operation == "stat":
                        p.stat()
                    elif operation == "open":
                        with p.open("rb"):
                            pass
                    elif operation == "read-byte":
                        with p.open("rb") as f:
                            f.read(1)
                    else:
                        st = p.stat()
                        fd = open_root(tmp)
                        try:
                            result = refresh_one(fd, os.fsencode(p.name),
                                {"dev": st.st_dev, "ino": st.st_ino, "mtime": st.st_mtime_ns,
                                 "ctime": st.st_ctime_ns, "size": st.st_size},
                                {"owner_only": True, "method": "utime", "freshness_tolerance_seconds": 5})
                            reason = result
                        finally:
                            os.close(fd)
                    rows.append({"operation": operation, "empty": empty, "initial_age_seconds": age,
                                 "before": before, "after": stamps(p), "result": reason})
        report = {"disposable_files_only": True, "observations": rows,
                  "limitations": "one client/node and instant; cache, relatime/noatime, deferred writeback and purge-side metadata may differ; no permission or retention conclusion"}
        atomic_json(output / "probe-report.json", report)
        return report
