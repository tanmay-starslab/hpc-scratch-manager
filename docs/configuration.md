# Configuration reference

The CLI accepts `--config /absolute/private/config.json` **before** the subcommand.
Start with [the sanitized example](../examples/config.example.json). JSON is strict:
unknown keys fail, paths must be absolute, shell variables/`~` are not expanded, and the
file must be owned by your UID with mode 600. All omitted values below take their defaults.
Do not store a real configuration in the public clone.

| Key | Default | Meaning |
| --- | --- | --- |
| `roots` | required nonempty list | Explicit directories to inventory; no overlapping roots or `/`; every component must be a real directory |
| `state_dir` | required | Private persistent mode-700 directory, separate from all roots; checkpoints, reports, state and Slurm output live here |
| `exclude` | `[]` | Case-sensitive root-relative path or basename globs (`fnmatch`, so `*` can span `/`); matching directories are pruned, including all descendants |
| `interval_days` | `60` | Positive elapsed 86400-second days after a matching successful refresh completion |
| `threshold_days` | `90` | Atime-based estimate only, not confirmed purge criteria |
| `warning_days` | `30` | Lead time for approaching-threshold counts; between zero and threshold |
| `owner_only` | `true` | Select only files owned by the executing UID; checked again during refresh |
| `workers` | `1` | Maximum concurrent refresh workers, 1–8; traversal is serial |
| `operations_per_second` | `20` | Aggregate pacing of new metadata-entry work and refresh submissions, not individual syscalls; positive |
| `max_entries` | `100000` | Maximum new inventory entries plus refresh attempts per invocation, 1–10000000 |
| `max_seconds` | `1800` | Cooperative elapsed-work budget per invocation; positive; not a kernel I/O timeout |
| `max_depth` | `64` | Maximum traversed directory depth below root, 1–128; hitting it is a visible partial failure |
| `refresh_enabled` | `false` | Explicit refresh gate; CLI still needs `--execute` or a valid scheduled token |
| `site_approval` | `""` | Private record of site-approved scope/method and validity; nonblank required for execution |
| `behavior_confirmed` | `false` | Operator attestation of site/probe-confirmed behavior for the selected method |
| `method` | `read-byte` | Exactly `read-byte` or `utime`; no fallback, full-file reading or rewriting |
| `freshness_tolerance_seconds` | `5` | Positive comparison allowance for timestamp precision; avoid increasing to hide failure |
| `scheduler_enabled` | `false` | Explicit daily-trigger gate |
| `scheduler_approval` | `""` | Private record of permission for the trigger, host, Slurm submission and resources |
| `sbatch` | `/usr/bin/sbatch` | Absolute executable, discover actual site path |
| `squeue` | `/usr/bin/squeue` | Absolute executable, discover actual site path |
| `job_script` | `""` | Absolute private wrapper path; required for trigger |
| `sbatch_args` | `[]` | Separate `--key=value` arguments; only `partition`, `account`, `qos`, `time`, `mem`, `cpus-per-task` accepted; obtain values from RC |
| `retry_hours` | `24` | Minimum positive hours between submissions after a job disappears without success |
| `max_attempts` | `3` | Total submissions in a failed cycle, including initial submission, 1–20; successful cycles reset this logically |
| `stale_hours` | `48` | Positive age for stale-running/old-submission warnings; does not authorize resubmission |

Limits produce exit 2 and preserve an unfinished checkpoint. Use identical configuration
with `--resume`, or start a new scan deliberately. Every configuration change invalidates
checkpoint compatibility and due coverage, including scheduler settings. Configure the
scheduler fully before the first approved scheduled cycle. Never edit selection/method
while a job is queued/running; stop it and reconcile first.

Budget defaults are conservative starting points, not a guarantee that a large tree fits
in three jobs. An entry can require multiple syscalls; filesystem latency and I/O blocked
in the kernel may exceed the elapsed budget. Tune job walltime, limits and retry budget
with RC after measuring a small synthetic or approved audit. Leave margin for report
export and shutdown. Persistent storage must have space for a row and report per path;
reports can be large and contain sensitive paths.

Refresh refuses files with more than one hard link because an outside-root alias cannot
be ruled out. Audit counts paths and logical sizes (not allocated disk blocks or unique
inodes). A missing/unreadable selected file is a failure; excluded files, foreign owners,
symlinks, special files and filesystem boundaries are reported skips.
