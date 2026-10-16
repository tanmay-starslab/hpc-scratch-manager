"""Linux openat2 descriptor-relative boundaries; fail closed if unavailable."""
import ctypes
import errno
import fnmatch
import os
import platform
import stat

NO_XDEV, NO_SYMLINKS, BENEATH = 0x01, 0x04, 0x08
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


class How(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


def secure_open(base, path, flags, resolve=BENEATH | NO_SYMLINKS | NO_XDEV):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise OSError(errno.ENOSYS, "requires Linux x86_64/aarch64 with openat2; no unsafe fallback")
    libc = ctypes.CDLL(None, use_errno=True)
    how = How(flags, 0, resolve)
    fd = libc.syscall(ctypes.c_long(437), ctypes.c_int(base), ctypes.c_char_p(os.fsencode(path)),
                      ctypes.byref(how), ctypes.sizeof(how))
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), os.fsdecode(path))
    return fd


def open_root(path):
    return secure_open(-100, path, DIRECTORY, NO_SYMLINKS)


def excluded(rel, patterns):
    # Both the root-relative path and basename are matched; excluded directories are pruned.
    name = os.fsdecode(rel)
    return any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(name.rsplit("/", 1)[-1], p)
               for p in patterns)


def same_file(st, row):
    return (st.st_dev, st.st_ino) == (row["dev"], row["ino"]) and stat.S_ISREG(st.st_mode)


def refresh_one(root_fd, rel, row, c):
    """Open only an already inventoried regular file. Never write file contents."""
    fd, pinned = None, None
    try:
        # O_PATH never opens a substituted device/FIFO for I/O. Validate type before
        # obtaining a readable handle to this exact pinned inode via trusted procfs.
        pinned = secure_open(root_fd, rel, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(pinned)
        if not same_file(before, row) or (c["owner_only"] and before.st_uid != os.getuid()):
            return {"outcome": "failed", "reason": "identity, type or owner changed"}
        if before.st_nlink != 1:
            return {"outcome": "failed", "reason": "hard-linked file; outside aliases cannot be ruled out"}
        # A concurrent writer invalidates the inventory; do not restore an old mtime.
        if (before.st_mtime_ns, before.st_size, before.st_ctime_ns) != (row["mtime"], row["size"], row["ctime"]):
            return {"outcome": "failed", "reason": "file changed since inventory"}
        fd = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        if not same_file(os.fstat(fd), row):
            return {"outcome": "failed", "reason": "pinned descriptor identity mismatch"}
        import time
        started = time.time_ns()
        if c["method"] == "read-byte":
            if before.st_size == 0:
                return {"outcome": "unverified", "reason": "empty file; read-byte cannot establish access"}
            if len(os.read(fd, 1)) != 1:
                return {"outcome": "unverified", "reason": "no byte read"}
        else:
            # Linux UTIME_OMIT preserves mtime atomically, even if another writer updates it.
            class Timespec(ctypes.Structure):
                _fields_ = [("sec", ctypes.c_long), ("nsec", ctypes.c_long)]
            ts = (Timespec * 2)(Timespec(started // 10**9, started % 10**9), Timespec(0, (1 << 30) - 2))
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.futimens(fd, ts) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
        after = os.fstat(fd)
        result = {"before_atime_ns": before.st_atime_ns, "after_atime_ns": after.st_atime_ns,
                  "mtime_preserved": after.st_mtime_ns == before.st_mtime_ns,
                  "ctime_changed": after.st_ctime_ns != before.st_ctime_ns}
        if after.st_mtime_ns != before.st_mtime_ns or after.st_size != before.st_size:
            return result | {"outcome": "failed", "reason": "concurrent change detected; no restoration attempted"}
        tolerance = int(c["freshness_tolerance_seconds"] * 10**9)
        if after.st_atime_ns < started - tolerance or after.st_atime_ns > time.time_ns() + tolerance:
            return result | {"outcome": "unverified", "reason": "atime not observed near current time"}
        return result | {"outcome": "verified", "reason": "current atime observed; not a purge guarantee"}
    except OSError as e:
        return {"outcome": "failed", "reason": f"{type(e).__name__}: errno {e.errno}"}
    finally:
        if fd is not None:
            os.close(fd)
        if pinned is not None:
            os.close(pinned)
