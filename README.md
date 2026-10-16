# hpc-scratch-manager

A small Python CLI for metadata-only HPC scratch audits, disposable timestamp probes,
and **optional, policy-gated, verified file access**. No runtime Python dependencies.
Linux x86_64/aarch64 with working `openat2` (kernel 5.6+), procfs, and Python 3.10+ required.
Unsupported filesystem/security features fail closed.

**Start with an immediate audit on approved compute/transfer resources. Refresh and
scheduling are disabled by default.** This project does not establish permission to
extend scratch retention, promise retention, provide a backup, or delete research files.

ASU describes scratch as temporary and removable after inactivity. Its active-use
language is not explicit authorization for artificial periodic access. Obtain an RC
decision for your use case, timestamp method, and scheduler before enabling automation.
See [documented facts, questions, and alternatives](docs/policy.md), with official sources.

## Quick start

From a clone, create a virtual environment and install locally:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m unittest discover -s tests -v
```

Create private settings **outside the clone and outside scratch**:

```bash
umask 077
mkdir -p "$HOME/.config/hpc-scratch-manager"
cp examples/config.example.json "$HOME/.config/hpc-scratch-manager/config.json"
chmod 600 "$HOME/.config/hpc-scratch-manager/config.json"
```

Edit that private file to replace the placeholder roots and persistent `state_dir` with
absolute, real paths. Keep both enable switches false. Confirm all path components are
real directories; symlink roots/parents are deliberately rejected. Settings are not
shell-expanded. Confirm the selected scratch filesystem and an approved execution node.
Then, **inside an RC-approved allocation**, run:

```bash
.venv/bin/hpc-scratch-manager --config "$HOME/.config/hpc-scratch-manager/config.json" audit
.venv/bin/hpc-scratch-manager --config "$HOME/.config/hpc-scratch-manager/config.json" refresh
.venv/bin/hpc-scratch-manager --config "$HOME/.config/hpc-scratch-manager/config.json" status
```

`refresh` above is a dry run: metadata only, no file contents opened or timestamps set.
`audit` reports selected regular-file counts, logical bytes, access-age buckets and
approaching/over-threshold counts. JSONL reports contain individual **estimated** purge
times, never a claim about the site's actual deletion logic. Traversal may update
**directory** atimes even in an audit or dry run.

Commands exit `0` on complete success, `2` for partial/unverified/limited work, `1` on
errors or configuration problems, and `130` for handled interruption. Use `--resume`
with `audit` or `refresh` to continue a matching unfinished checkpoint. A successful dry
run or audit never records successful refresh coverage. New files after inventory need
a new scan; this is not a snapshot.

## Commands

| Command | Behavior |
| --- | --- |
| `audit [--resume]` | Streaming metadata inventory; private SQLite checkpoint and JSONL report |
| `probe --test-dir ABS --output-dir ABS` | Unique disposable child only; records atime/mtime/ctime before and after listing, stat, open, read, explicit atime update |
| `refresh [--resume]` | Audit-like dry run; no file data access |
| `refresh --execute [--resume]` | Requires `refresh_enabled`, `site_approval`, `behavior_confirmed`; uses exactly one configured method |
| `status` | State-only view of attempts, success, next due run, partial/stale/missing state and submission |
| `trigger` | Approved daily trigger: state/queue check, due-only Slurm submission, no scratch traversal |
| `reset-retries` | Explicit recovery after reconciling/cancelling old jobs; revokes old submission tokens |

For `probe`, omit `--config`; select a dedicated test parent on the target filesystem
and an output directory in persistent storage. It changes timestamps only on files it
creates. Read [probe interpretation](docs/filesystem.md) before selecting a refresh method.
A one-byte read avoids full-file reads; it cannot establish access for empty files.
An approved explicit atime update handles empty files and avoids payload I/O, preserves
mtime with `UTIME_OMIT`, and normally changes ctime. No automatic fallback is used.

## Unattended operation

The configurable default is **60 elapsed days after successful completion**. A lightweight
daily trigger reads persistent state and checks the queue; it never inventories scratch.
A queued job is distinct from a successful run. Bounded retries and two locks prevent
routine overlaps; durable submission intent blocks blind resubmission after ambiguity.

Slurm batch execution is documented at ASU. Cron availability/permission was **not
established** by the reviewed documentation. Install the provided daily cron example
only on a host and under conditions RC approves, or ask RC to invoke the same trigger
with its supported recurring scheduler. Nothing requires Orca, a laptop, an agent, or
an open SSH session after installation. No GitHub Actions cluster credentials are used.

A 60-day interval is **not a retention guarantee**: queues, outages, partial runs,
files already near expiry, and actual purge rules can defeat it. [Deployment and
scheduling](docs/deployment.md) explains the initial audit, approval, wrappers, monitoring,
recovery, and uninstall without removing unrelated cron jobs.

## More documentation

- [Policy facts, unresolved questions, storage alternatives](docs/policy.md)
- [Beginner Orca-to-Sol installation and operation](docs/deployment.md)
- [Every configuration setting](docs/configuration.md)
- [Timestamp semantics and probes](docs/filesystem.md)
- [Architecture, boundaries, checkpoints and limitations](docs/architecture.md)
- [Troubleshooting and recovery](docs/troubleshooting.md)
- [Contributing and synthetic validation](CONTRIBUTING.md)

Keep real settings, reports, credentials and runtime files outside this public
repository. `.gitignore` is a second line of defense, not permission to commit personal
data. Do not paste logs or research filenames into public issues. License: [MIT](LICENSE).
