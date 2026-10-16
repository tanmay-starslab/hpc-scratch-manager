# Architecture and operational boundaries

The standard-library-only Python CLI has six small components: strict configuration,
Linux descriptor-based filesystem operations, a streaming inventory/refresh engine,
durable private state, a disposable probe, and a state-only Slurm trigger. The shell
wrappers contain no retention logic and request no hardcoded site resources.

## Filesystem boundaries

Each explicit root is opened without symlinks in **any** component. For every path
inside it, Linux [`openat2`](https://man7.org/linux/man-pages/man2/openat2.2.html) uses
`RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV`. This rejects escapes, substituted
ancestor symlinks and mount crossings, including same-device bind mounts. No unsafe
path-based fallback is provided on older/blocked kernels. Mounts encountered during
traversal are recorded as skips. All roots must be real, stable mounted directories.

Audit uses directory descriptors, `scandir`, no-follow stat and `O_PATH` metadata
handles; it never obtains a payload descriptor or sets timestamps. A refresh first
obtains an `O_PATH` handle, checks it is the inventoried regular inode, owned as configured,
with a single hard link, then obtains a readable handle to **that pinned inode** through
`/proc/self/fd/N`. This deliberately uses trusted Linux procfs; it never reopens an
untrusted pathname for payload I/O. This prevents a file swapped for a device/FIFO from
being opened for I/O before type checks. Symlinks, sockets, devices and FIFOs are never
refresh targets. Payload operations use read-only descriptors; utime changes only metadata.

The boundary is the pinned root directory identity for the invocation. An administrator
or the file owner can concurrently rename/unlink an already opened inode or directory;
an in-flight descriptor continues to refer to that inode. This is normal descriptor
semantics, not a filesystem snapshot or a defense against a malicious administrator.
The program rejects root identity changes across resumes. Keep roots stable and avoid
concurrent tree rearrangement. Hard links are refused for refresh because path isolation
cannot exclude aliases elsewhere. Never run this utility as root.

## Inventory and checkpoints

`audit.sqlite`, `refresh-dry.sqlite` and `refresh.sqlite` each contain a disk-backed
frontier and inventory. Paths are BLOBs (filesystem bytes), not newline-delimited shell
arguments. JSON exports escape unusual characters, newlines and undecodable bytes.
No list of all files is held in memory. Directory entries stream serially; a pending
directory is restarted after interruption, with committed paths deduplicated. Committed
names are looked up locally without stat/payload access during replay. A very large
single directory can still make replay expensive; size limits should be calibrated.

An invocation handles at most the configured entry/time budget. Traversal commits every
100 units of new work and at directory boundaries. Refresh uses at most `workers`
outstanding futures and commits each outcome. SQLite uses full synchronous transactions
and DELETE journals, not WAL. Require a persistent filesystem with reliable SQLite and
advisory lock support; shared NFS behavior must be confirmed with RC. Row storage and
complete JSONL exports require disk space proportional to paths.

The engine scans and processes within one invocation. If budget runs out, a later resume
continues enumeration and outstanding files. A processed row is not reaccessed. Failed
or unverified file/directory rows are retried on an explicit resume after a complete
partial run; metadata errors trigger safe re-enumeration. A file that changed since
inventory may require a fresh inventory, rather than automatic reuse of stale metadata.
Depth-limit failures require a revised configuration/new scan or a narrower selection.

There is no transaction spanning a filesystem action and SQLite: a crash after access
but before committing its result may cause an extra access on replay. The methods are
idempotent with respect to content/mtime, but an explicit update can change ctime again.
No exactly-once guarantee is claimed. Files created after their directory was committed
are not covered until a fresh inventory. Removed/unreadable files are visible failures,
not silently counted as verified. Counts/ages use inventory-time metadata, including
after refresh; per-file details carry the observed after-atime separately.

## Locks and state

`work.lock` covers audits, dry runs and refresh across processes. `trigger.lock` covers
queue queries and submission. `reset-retries` needs both. These are nonblocking `flock`
locks; the kernel releases them after process exit. Never unlink a lock file to bypass
it: existing processes may still hold a lock on the old inode.

`state.json` is replaced atomically after fsync, with separate run records for audit,
dry-run and real refresh. It records attempt, completion, outcome, summary and last
successful completion. Only a complete run without failed/unverified selected entries
advances refresh success. Reports are streamed to a private temporary file then replaced;
SQLite is authoritative if report export is interrupted. SIGINT/SIGTERM preserve committed
checkpoints. SIGKILL can leave an old `running` record, and status flags its age.

The daily trigger performs no scratch traversal. It calculates due time from a successful
refresh's completion + `interval_days * 86400`, checks current-user jobs under a deterministic
state-directory-derived name, and persists a submission token **before** `sbatch`. No shell
interpolation is used. It distinguishes queue submission from completion recorded by the
job. A valid token and unchanged configuration are required for the job to start. Scheduled
jobs automatically resume matching unfinished checkpoints and start a new scan after a
completed one. Different configs for the same selection must share one state directory;
separate state directories are independent installations and can overlap.

A crash/timeout around submission is ambiguous. With a visible named job, trigger adopts
its ID and does not duplicate it. With no visible job and an uncertain intent, it blocks
until the operator checks Slurm accounting and explicitly resets. A known accepted job
that leaves the queue without success can retry after backoff, up to the configured total
submission budget. Slurm query failures stop submission. This assumes accurate same-cluster
queue visibility and no outside jobs using the reserved name; federated submissions are
unsupported and blocked for manual reconciliation. A Slurm walltime/kill can interrupt
work; blocked kernel I/O is not safely cancellable in Python.

A 60-day cadence anchored at completion is approximate for individual files: the first
file in a long scan can be older than 60 days by the time the next run reaches it.
Newly added/near-expiry files, job queue delays and the site's real purge decision are
additional gaps. Use small selections, bounded run duration, immediate audits and the
site's own alerts; obtain durable storage for data that must be kept.
