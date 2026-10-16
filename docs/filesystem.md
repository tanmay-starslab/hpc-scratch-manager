# Timestamp behavior and disposable probes

`atime` is the reported access timestamp, `mtime` is the content-modification timestamp,
and `ctime` tracks inode status changes (it is **not** creation time). Reading attributes
is different from reading bytes. These distinctions do not determine a site's purge rule.

| Action | What it does | Typical observation, subject to filesystem rules |
| --- | --- | --- |
| Directory listing | Reads directory entries/names | May update directory atime; does not read each file's payload |
| `stat` / metadata descriptor | Obtains file attributes | Does not intentionally refresh file atime |
| Opening read-only | Obtains an I/O descriptor | Opening alone need not update file atime |
| Reading one byte | Performs a payload read | May update atime; mtime unchanged; a zero-byte file has no byte to read |
| Explicit atime update | Sets atime through `futimens` | Preserves mtime with `UTIME_OMIT`; normally changes ctime, requires permission |

Linux `noatime` suppresses ordinary read-driven access-time updates. `relatime` updates
them conditionally, such as when atime predates mtime/ctime or is sufficiently old.
Other server/client policies, lazy writeback, caching, timestamp resolution and clock
skew matter too. See the primary Linux manuals for [mount semantics](https://man7.org/linux/man-pages/man2/mount.2.html)
and [timestamp updates](https://man7.org/linux/man-pages/man2/utimensat.2.html).

## Probe

On the approved target resource, choose a dedicated test parent on the intended
filesystem; **never point a timestamp experiment at research files**:

```bash
umask 077
mkdir -p /scratch/REPLACE_WITH_USER/disposable-atime-tests
.venv/bin/hpc-scratch-manager probe \
  --test-dir /scratch/REPLACE_WITH_USER/disposable-atime-tests \
  --output-dir "$HOME/.local/state/hpc-scratch-manager-probe"
```

The command creates a unique mode-700 child, only manipulates its own new synthetic
files, and removes that child afterward. It tests empty/nonempty files with old and
recent atimes. Each observation records before/after atime, mtime and ctime for listing,
stat, open, a short read, and explicit atime setting. A persistent private
`probe-report.json` contains the results; repeated probes replace this report. Initial
backdating is done only to these disposable files. It may affect ctime and interact
with relatime, so the probe is not an exact simulation of 60 days of inactivity.

A single probe cannot prove behavior across nodes, caches, mount configurations or
future upgrades, show what a purge service sees, or grant permission. Compare approved
client/server observations over time with RC. Confirm the installed Linux kernel supports
`openat2`, that procfs is available, and that these APIs work on the target filesystem.
No inference from the local development tests establishes Sol behavior.

## Selecting the least expensive approved method

Use `utime` only if RC specifically permits explicit atime updates and accepts their
ctime effects and actual purge relevance. It avoids payload reads, works for empty files,
and preserves mtime atomically. It still generates metadata traffic. If RC instead
permits ordinary access, and confirms a short read is sufficient, `read-byte` reads at
most **one byte per selected nonempty file**. Metadata/open costs may dominate this byte.
If neither is both permitted and effective, leave refresh disabled. The program does not
auto-select a method from one probe and never falls back from one method to the other.

Every attempted refresh pins and rechecks the inventoried inode, file type, ownership,
link count, size, mtime and ctime. It uses the exact selected operation, then checks that
reported atime is near wall-clock now and that size/mtime stayed unchanged. `verified`
means **a current atime was observed**, not that it definitely advanced, reached stable
server storage, or will prevent deletion. The check allows configured timestamp precision.
An old/unexpected atime is `unverified`. Empty `read-byte` files are explicitly unverified
without a fallback. A file changed concurrently is failed; the tool never restores an
old mtime over a writer's update.

Neither method writes file contents. Ordinary reads normally preserve ctime; explicit
atime updates normally change it. Concurrent application writes cannot be prevented by
this tool's advisory lock. Do not refresh actively changing trees, and do not use
refresh as evidence of content integrity: verifying unchanged bytes would require
reading them, which this project intentionally avoids.
