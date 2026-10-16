"""Streaming inventory, disk-backed frontier and resumable bounded refresh batches."""
from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
import sqlite3
import stat
import time

from .config import require_refresh
from .safe_fs import DIRECTORY, excluded, open_root, refresh_one, secure_open
from .state import atomic_json, locked, private_dir, read_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entries (
 root INTEGER, rel BLOB, kind TEXT, dev INTEGER, ino INTEGER, size INTEGER,
 atime INTEGER, mtime INTEGER, ctime INTEGER, uid INTEGER, done INTEGER DEFAULT 0,
 outcome TEXT, reason TEXT, detail TEXT, PRIMARY KEY(root,rel));
CREATE INDEX IF NOT EXISTS todo ON entries(kind,done);
"""


class Budget:
    def __init__(self, c):
        self.c, self.started, self.used = c, time.monotonic(), 0
        self.next_op = self.started

    def exhausted(self):
        return self.used >= self.c["max_entries"] or time.monotonic() - self.started >= self.c["max_seconds"]

    def tick(self):
        # Aggregate rate across scanning and worker submissions, rather than one rate per worker.
        delay = self.next_op - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.next_op = max(self.next_op, time.monotonic()) + 1 / self.c["operations_per_second"]
        self.used += 1


def db_open(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    # DELETE journal works with traditional network filesystem locking; confirm support with RC.
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript(SCHEMA)
    return db


def insert(db, root, rel, kind, st, outcome=None, reason=None):
    cur = db.execute("""INSERT OR IGNORE INTO entries
        (root,rel,kind,dev,ino,size,atime,mtime,ctime,uid,outcome,reason)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (root, rel, kind, st.st_dev, st.st_ino,
        st.st_size, st.st_atime_ns, st.st_mtime_ns, st.st_ctime_ns, st.st_uid, outcome, reason))
    return bool(cur.rowcount)


def mark(db, row, outcome, reason, detail=None):
    db.execute("UPDATE entries SET done=1,outcome=?,reason=?,detail=? WHERE root=? AND rel=?",
               (outcome, reason, json.dumps(detail) if detail else None, row["root"], row["rel"]))


def inventory(db, fds, c, budget):
    while not budget.exhausted():
        row = db.execute("SELECT * FROM entries WHERE kind='dir' AND done=0 LIMIT 1").fetchone()
        if row is None:
            return True
        fd = None
        try:
            fd = secure_open(fds[row["root"]], row["rel"] or b".", DIRECTORY)
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != (row["dev"], row["ino"]):
                mark(db, row, "failed", "directory identity changed")
                db.commit()
                continue
            complete = True
            with os.scandir(fd) as entries:
                for entry in entries:
                    rel = row["rel"] + (b"/" if row["rel"] else b"") + os.fsencode(entry.name)
                    if budget.exhausted():
                        complete = False
                        break
                    if db.execute("SELECT 1 FROM entries WHERE root=? AND rel=?", (row["root"], rel)).fetchone():
                        # Replayed directory names: local checkpoint lookup only, no file stat.
                        # Do not consume the new-work budget or sleep again for committed entries.
                        continue
                    budget.tick()
                    try:
                        st = entry.stat(follow_symlinks=False)
                        kind = "skip"
                        outcome, reason = "skipped", "special file or symlink"
                        if excluded(rel, c["exclude"]):
                            reason = "excluded"
                        elif st.st_dev != os.fstat(fds[row["root"]]).st_dev:
                            reason = "filesystem boundary"
                        elif stat.S_ISDIR(st.st_mode):
                            if rel.count(b"/") + 1 > c["max_depth"]:
                                outcome, reason = "failed", "maximum depth reached"
                            else:
                                kind, outcome, reason = "dir", None, None
                        elif stat.S_ISREG(st.st_mode):
                            if c["owner_only"] and st.st_uid != os.getuid():
                                reason = "not owned by executing user"
                            else:
                                # Metadata descriptor: also rejects file bind mounts without reading contents.
                                pfd = secure_open(fds[row["root"]], rel, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
                                try:
                                    current = os.fstat(pfd)
                                    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
                                        raise OSError(errno.ESTALE, "file identity changed")
                                finally:
                                    os.close(pfd)
                                kind, outcome, reason = "file", None, None
                        insert(db, row["root"], rel, kind, st, outcome, reason)
                    except OSError as e:
                        # Disappearance/unreadability is a visible partial result, never success.
                        db.execute("""INSERT OR IGNORE INTO entries
                            (root,rel,kind,done,outcome,reason) VALUES (?,?,'error',1,'failed',?)""",
                            (row["root"], rel, f"metadata errno {e.errno}"))
                    if budget.used % 100 == 0:
                        db.commit()
            if complete:
                mark(db, row, "scanned", "directory enumerated")
            db.commit()
            if not complete:
                return False
        except OSError as e:
            # A mounted subtree is excluded even if device numbers happen to match (bind mounts).
            mark(db, row, "skipped" if e.errno == errno.EXDEV else "failed", f"directory errno {e.errno}")
            db.commit()
        finally:
            if fd is not None:
                os.close(fd)
    return not db.execute("SELECT 1 FROM entries WHERE kind='dir' AND done=0").fetchone()


def process(db, fds, c, budget):
    with ThreadPoolExecutor(max_workers=c["workers"]) as pool:
        while not budget.exhausted():
            rows = db.execute("SELECT * FROM entries WHERE kind='file' AND done=0 LIMIT ?", (c["workers"],)).fetchall()
            if not rows:
                return True
            pending = []
            for row in rows:
                if budget.exhausted():
                    break
                budget.tick()
                pending.append((row, pool.submit(refresh_one, fds[row["root"]], row["rel"], row, c)))
            for row, future in pending:
                result = future.result()
                mark(db, row, result["outcome"], result["reason"], result)
                db.commit()
    return not db.execute("SELECT 1 FROM entries WHERE kind='file' AND done=0").fetchone()


def summary(db, c, complete):
    now_ns = time.time_ns()
    n, size, oldest, youngest = db.execute("SELECT count(*),coalesce(sum(size),0),min(atime),max(atime) FROM entries WHERE kind='file'").fetchone()
    cutoff = now_ns - int((c["threshold_days"] - c["warning_days"]) * 86400 * 1e9)
    expired = now_ns - int(c["threshold_days"] * 86400 * 1e9)
    counts = dict(db.execute("SELECT coalesce(outcome,'pending'),count(*) FROM entries GROUP BY outcome"))
    ages = {"0_to_30_days": 0, "30_to_60_days": 0, "60_to_90_days": 0, "90_plus_days": 0}
    for atime, count in db.execute("SELECT CAST((?-atime)/86400000000000 AS INTEGER) AS age,count(*) FROM entries WHERE kind='file' GROUP BY age", (now_ns,)):
        ages[next(k for low, k in ((90, "90_plus_days"), (60, "60_to_90_days"), (30, "30_to_60_days"), (-10**12, "0_to_30_days")) if atime >= low)] += count
    return {"complete": complete, "files": n, "bytes": size, "access_age_buckets": ages,
            "oldest_access_age_days": (now_ns - oldest) / 86400e9 if oldest is not None else None,
            "youngest_access_age_days": (now_ns - youngest) / 86400e9 if youngest is not None else None,
            "approaching_threshold": db.execute("SELECT count(*) FROM entries WHERE kind='file' AND atime<=? AND atime>?", (cutoff, expired)).fetchone()[0],
            "at_or_over_threshold": db.execute("SELECT count(*) FROM entries WHERE kind='file' AND atime<=?", (expired,)).fetchone()[0],
            "outcomes": counts, "purge_dates": "ESTIMATES: metadata atime + configured threshold; actual site criteria unconfirmed",
            "note": "inventory-time metadata; traversal may update directory atime; no retention guarantee"}


def export(db, path, c):
    # A fresh atomic report avoids duplicate lines after checkpoint replay. DB remains authoritative.
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            for row in db.execute("SELECT * FROM entries"):
                item = dict(row)
                item["relative_path"] = os.fsdecode(item.pop("rel"))
                if item.get("atime") is not None and item["kind"] == "file":
                    item["estimated_purge_time_ns"] = item["atime"] + int(c["threshold_days"] * 86400e9)
                    item["purge_date_is_estimate"] = True
                f.write(json.dumps(item, ensure_ascii=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(c, mode, execute=False, resume=False, token=None):
    if execute:
        require_refresh(c)
    name = "refresh" if execute else "refresh-dry" if mode == "refresh" else "audit"
    directory = private_dir(c["state_dir"])
    with locked(directory / "work.lock"):
        if token:
            submission = read_json(directory / "submission.json")
            if (token != submission.get("token") or submission.get("fingerprint") != c["fingerprint"]
                    or submission.get("status") not in ("submitting", "queued")):
                raise ValueError("scheduled job token no longer authorized")
        if resume is None:
            checkpoint = directory / f"{name}.sqlite"
            if checkpoint.exists():
                db = db_open(checkpoint)
                try:
                    meta = dict(db.execute("SELECT key,value FROM meta"))
                    resume = meta.get("fingerprint") == c["fingerprint"] and meta.get("finished") != "yes"
                finally:
                    db.close()
            else:
                resume = False
        return _run(c, name, execute, resume, directory)


def _run(c, name, execute, resume, directory):
    state_path = directory / "state.json"
    state = read_json(state_path)
    runs = state.setdefault("runs", {})
    previous = runs.get(name, {})
    attempt = {"last_attempt": time.time(), "last_success": previous.get("last_success"),
               "fingerprint": c["fingerprint"], "outcome": "running"}
    # Never inherit successful coverage from a different configuration.
    if previous.get("fingerprint") != c["fingerprint"]:
        attempt["last_success"] = None
    runs[name] = attempt
    atomic_json(state_path, state)
    db = db_open(directory / f"{name}.sqlite")
    fds = []
    result = None
    try:
        old = dict(db.execute("SELECT key,value FROM meta"))
        if resume:
            if old.get("fingerprint") != c["fingerprint"] or old.get("finished") == "yes":
                raise ValueError("no matching unfinished checkpoint; omit --resume for a fresh inventory")
        else:
            db.execute("DELETE FROM entries")
            db.execute("DELETE FROM meta")
            db.execute("INSERT INTO meta VALUES ('fingerprint',?)", (c["fingerprint"],))
        for i, root in enumerate(c["roots"]):
            fd = open_root(root)
            fds.append(fd)
            st = os.fstat(fd)
            identity = f"{st.st_dev}:{st.st_ino}"
            if resume and old.get(f"root:{i}") != identity:
                raise ValueError("root identity changed; fresh inventory required")
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"root:{i}", identity))
            insert(db, i, b"", "dir", st)
        db.commit()
        budget = Budget(c)
        scanned = inventory(db, fds, c, budget)
        processed = process(db, fds, c, budget) if execute else True
        if not execute:
            db.execute("UPDATE entries SET done=1,outcome='inventoried' WHERE kind='file'")
            db.commit()
        result = summary(db, c, scanned and processed)
        outcomes = result["outcomes"]
        success = result["complete"] and not outcomes.get("failed") and not outcomes.get("unverified")
        attempt["outcome"] = "success" if success else "partial"
        attempt["last_completed"] = time.time()
        attempt["summary"] = result
        # Export outcomes before preparing retry rows, so disappearing-path errors stay visible.
        export(db, directory / f"{name}-report.jsonl", c)
        if success:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('finished','yes')")
        elif result["complete"]:
            # Explicit --resume retries failed/unverified records only; verified files are never reaccessed.
            db.execute("UPDATE entries SET done=0 WHERE kind IN ('file','dir') AND outcome IN ('failed','unverified')")
            # Metadata errors need a new listing to discover the current object safely.
            if db.execute("SELECT 1 FROM entries WHERE kind='error'").fetchone():
                db.execute("DELETE FROM entries WHERE kind='error'")
                db.execute("UPDATE entries SET done=0 WHERE kind='dir'")
        db.commit()
        if success:
            attempt["last_success"] = time.time()
        return result, 0 if success else 2
    except BaseException as e:
        db.rollback()
        attempt["outcome"] = "interrupted" if isinstance(e, (KeyboardInterrupt, SystemExit)) else "failed"
        attempt["error"] = str(e)
        raise
    finally:
        for fd in fds:
            os.close(fd)
        db.close()
        atomic_json(state_path, state)
