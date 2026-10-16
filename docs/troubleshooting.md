# Troubleshooting

Keep logs private; redact paths, names, IDs and approval references before sharing.

| Symptom | What to check / do |
| --- | --- |
| `refresh requires ...` | Default gate is working. Obtain site permission and filesystem confirmation; do not fill approval fields speculatively |
| Audit/dry run exits 2 | Inspect private summary/report: budget exhaustion, depth, disappearing/unreadable files. Resume identical unfinished work or fix selection intentionally |
| `another operation holds this lock` | Check Slurm/processes; wait. Never delete the lock to bypass it |
| `ENOSYS`/errno 38 or openat2 unsupported | Use a supported Linux execution node/kernel or leave refresh disabled. There is no unsafe fallback |
| errno 40 (`ELOOP`) opening root/path | A path component is a symlink; choose the approved real path, not a symlink alias |
| errno 18 (`EXDEV`) | Root-relative resolution hit a filesystem/bind-mount boundary; intentionally skipped/rejected |
| Empty files unverified | `read-byte` cannot establish an access. An explicit atime method requires separate approval; no automatic switch |
| Read succeeded but old atime | Check noatime/relatime, client/server views and cache/writeback with RC. Never increase tolerance to cover days of staleness |
| `file changed since inventory` | A writer, rename or status update invalidated metadata. Stop tree rearrangement/writes; consider a new approved inventory, not timestamp restoration |
| Hard-linked file failed | Outside-root aliases cannot be ruled out. Use a retention/storage plan with RC; do not break research links just to pass checks |
| `no matching unfinished checkpoint` | Configuration changed, previous run finished, or checkpoint missing. Use a deliberate new run without `--resume` |
| Root identity changed | Root was replaced/remounted. Confirm correct mounted scratch and actual root with RC; start a fresh inventory |
| Report is absent/old after interruption | SQLite holds committed progress; rerun `--resume`. No completed report should be inferred from a stale file |
| `running` state but no process | SIGKILL/outage can leave this. Check Slurm/accounting, then resume; lock ownership, not the JSON label, prevents local overlap |
| SQLite lock/I/O/full disk errors | Stop overlapping instances; confirm persistent filesystem lock/atomicity support and free space with RC. Do not put state in scratch to save space |
| Job rejected, pending or cancelled | Review private sbatch arguments, valid allocation/QoS, resource fit, walltime and Slurm output with RC |
| Retry budget exhausted | Inspect every attempt and adjust approved limits. After queue/accounting reconciliation, `reset-retries` explicitly allows another cycle |
| Unknown submission / malformed sbatch response | It may have been accepted. Reconcile queue/accounting before reset; do not repeatedly submit manually |
| Trigger never runs | Check the approved host's scheduler, timezone, wrapper executable/Python path and trigger log. Status alone cannot monitor a dead scheduler |
| Config changed while queued | Stop trigger, cancel exact pending job, reconcile, then reset. Configure all settings before the next approved cycle |
| `Permission denied` | Verify ownership and private directory modes; mode-600 config and mode-700 state are required. Do not broaden permissions on research data |

`status` is safe to run without a scratch scan and does not open file contents. It shows
last real-refresh attempt/success and next due time; `runs` also contains audit/dry-run
history. Success from a different configuration does not establish current coverage.
A submission record is not proof the file operations completed.

If a method fails to refresh atime, there is no fallback to full-file reads, rewriting,
mtime changes, hard-link removal, copying between scratch systems or deleting files.
Ask RC for an effective permitted method or durable storage instead.

For corruption in state/checkpoints, first stop the trigger, reconcile/cancel exact jobs
and preserve the private files for inspection. Once no jobs hold locks, move damaged
state aside privately and start a deliberate new audit/inventory. Losing state makes
refresh immediately due; it is not a reason to submit blindly. Never edit checkpoint
rows to disguise failures. There is no automatic research-data deletion or cleanup.
