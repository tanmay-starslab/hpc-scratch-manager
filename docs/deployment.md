# Orca-to-Sol deployment for a beginner

Orca is a development terminal. The deployed Python environment, wrappers, configuration,
state and daily trigger must live on approved **cluster-side persistent storage**. Once
installed there, Slurm runs jobs independently of your editor, laptop, AI agent or SSH
session. This guide does not install anything on Sol by itself.

Uppercase `REPLACE_WITH_...` and `RC_...` values below are placeholders. Obtain actual
host/resource values from RC and edit them before running the associated command.
Do not post real private paths, names or command output to public issues.

## 1. Develop and verify in Orca

```bash
git clone https://github.com/OWNER/hpc-scratch-manager.git
cd hpc-scratch-manager
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m unittest discover -s tests -v
```

Use your normal approved Sol connection method (VPN/SSH or web portal as appropriate).
Keep credentials in existing secure authentication, never in this repository or chat.
Do not configure a GitHub Actions runner to access Sol. CI only uses temporary synthetic
files on hosted Linux runners.

## 2. Install a separate copy on Sol

Confirm a site-provided Python 3.10+ and Linux `openat2` support on the actual execution
resource. Module selection is site-specific; no module version is assumed. Run these
small installation steps where RC permits software setup:

```bash
umask 077
mkdir -p "$HOME/.local/opt" "$HOME/.config/hpc-scratch-manager"
mkdir -p "$HOME/.local/state/hpc-scratch-manager" "$HOME/.local/libexec/hpc-scratch-manager"
chmod 700 "$HOME/.local/state/hpc-scratch-manager" "$HOME/.local/libexec/hpc-scratch-manager"
git clone https://github.com/OWNER/hpc-scratch-manager.git "$HOME/.local/opt/hpc-scratch-manager"
cd "$HOME/.local/opt/hpc-scratch-manager"
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
cp examples/config.example.json "$HOME/.config/hpc-scratch-manager/config.json"
chmod 600 "$HOME/.config/hpc-scratch-manager/config.json"
```

Edit the private JSON (with `nano`, `vi`, or your editor). Set explicit selected scratch
roots and a persistent `state_dir`. Keep `refresh_enabled=false`, `behavior_confirmed=false`
and `scheduler_enabled=false`. Add exclusions, review ownership and conservative limits.
Do not place any runtime directory within scratch or the clone. The paths above are an
installation layout suggestion, not an assumption about your actual home mount; use
real paths approved by RC if these are symlinked or unsuitable.

## 3. Run an immediate audit before scheduling

A large metadata walk is a workload. **Do not run it on a login node.** Submit an audit
job on the compute/transfer resource RC selects, using the [documented Slurm batch model](https://docs.rc.asu.edu/slurm-sbatch/).
One way is to create this private script (no research paths committed):

```bash
cat > "$HOME/.local/libexec/hpc-scratch-manager/audit-job.sh" <<'SH'
#!/bin/bash
set -euo pipefail
umask 077
exec "$HOME/.local/opt/hpc-scratch-manager/.venv/bin/hpc-scratch-manager" \
  --config "$HOME/.config/hpc-scratch-manager/config.json" audit "$@"
SH
chmod 700 "$HOME/.local/libexec/hpc-scratch-manager/audit-job.sh"
```

Replace **all** resource placeholders with RC-confirmed values (omit an account/QoS
argument only if RC says to use a default):

```bash
sbatch --partition=RC_PARTITION --account=RC_ACCOUNT --qos=RC_QOS \
  --time=RC_WALLTIME --mem=RC_MEMORY --cpus-per-task=1 \
  --output="$HOME/.local/state/hpc-scratch-manager/audit-%j.log" \
  "$HOME/.local/libexec/hpc-scratch-manager/audit-job.sh"
```

Submission is not completion. Inspect `squeue -u "$USER"`, job output, and the private
`audit-report.jsonl` and `audit.sqlite`. If exit 2 is due to limits, submit the same script
with a final `--resume` argument and unchanged settings. A completed inventory can still
report inaccessible data or dangerous ages. Address near-expiry files immediately with
RC; do not wait 60 days. File-age estimates are advisory until purge criteria are known.

A status check is light and does not touch scratch:

```bash
"$HOME/.local/opt/hpc-scratch-manager/.venv/bin/hpc-scratch-manager" \
  --config "$HOME/.config/hpc-scratch-manager/config.json" status
```

## 4. Probe and choose a method with RC

Follow [the policy questions](policy.md) and [disposable probe instructions](filesystem.md).
Run the probe on approved resources, using a dedicated scratch test parent and a different
persistent output directory. It never experiments on existing research files. Recheck
behavior on the actual execution node/mount and discuss server-side purge relevance.

A manual `refresh` without `--execute` is another metadata-only dry run. Run it through
an approved allocation, just as with audit. Only when RC permits the workflow should
you set the private `site_approval`, `method`, `behavior_confirmed=true` and
`refresh_enabled=true`. Test `refresh --execute` on a small, approved selection first,
review its per-file outcomes, and check mtime preservation and current atimes. Never
silently switch methods to make failures disappear. Keep scheduler disabled until
manual operation and limits are understood.

## 5. Configure Slurm execution and the daily trigger

Copy and customize the two wrappers **outside the clone**:

```bash
cp scripts/slurm-job.sh scripts/daily-trigger.sh "$HOME/.local/libexec/hpc-scratch-manager/"
chmod 700 "$HOME/.local/libexec/hpc-scratch-manager/"*.sh
command -v sbatch
command -v squeue
```

Edit their absolute Python/config paths to the installation on Sol. Both wrappers use
`umask 077`. `slurm-job.sh` deliberately uses an absolute Python path because scheduled
jobs are submitted with `--export=NONE`; it must not depend on an interactive shell,
module initialization, Orca, current directory, or `PATH` activation. Check that its
Python dependencies are usable without your interactive environment. If a required
site module must be loaded, add RC's confirmed initialization to your private wrapper.

In the private JSON, set `job_script` and the actual `sbatch`/`squeue` executable paths.
Populate `sbatch_args` with RC-approved **separate `--key=value` strings**, for example:

```json
["--partition=RC_PARTITION", "--account=RC_ACCOUNT", "--qos=RC_QOS",
 "--time=RC_WALLTIME", "--mem=RC_MEMORY", "--cpus-per-task=1"]
```

Set total CPUs at least equal to `workers`. Allow budget plus export/shutdown time below
walltime. Confirm private state supports `flock`, SQLite locks/transactions and atomic
rename from both trigger and execution nodes. The same selected roots must use a single
installation/state directory; independent configs with separate state can overlap.

Only after separate scheduling approval, record `scheduler_approval` (host, trigger,
resource scope) and set `scheduler_enabled=true`. Configure everything before the first
scheduled cycle: any config change invalidates due/checkpoint compatibility. Do not
edit it while jobs are queued/running. Test the trigger once on the approved trigger
host:

```bash
"$HOME/.local/libexec/hpc-scratch-manager/daily-trigger.sh"
```

It returns `not-due`, `submitted`, `already-queued-or-running`, or `retry-backoff`, or
exits nonzero with a visible error. When due, it submits a job that automatically resumes
unfinished work or starts a fresh inventory. A successful complete refresh establishes
the next due time, exactly `completion + interval_days * 86400`. Three total attempts,
at least 24 hours apart, are the defaults for a failed cycle. An ambiguous submission
blocks automatic retries until reconciled.

### Conditional cron example

The official documentation reviewed establishes Slurm batch support; it does **not**
establish user cron availability, permission or a persistent host for cron. Ask RC which
recurring scheduler/host to use. If user cron is explicitly allowed, add **one** line
with `crontab -e` on that host, replacing each placeholder with an absolute private path:

```cron
17 4 * * * /home/REPLACE_WITH_USER/.local/libexec/hpc-scratch-manager/daily-trigger.sh >> /home/REPLACE_WITH_USER/.local/state/hpc-scratch-manager/trigger.log 2>&1 # HSM_PROJECT_DAILY
```

This is a daily local-time check, not a 60-day cron expression. `*/60` in day-of-month is
wrong, and two calendar months are not 60 elapsed days. The trigger reads durable state
rather than walking scratch daily. It runs no listener, sleep service or credentialed
GitHub runner. A cron line fails silently unless its private log/exit handling is
monitored; use a site-supported notification mechanism if available. Confirm cron's
actual timezone and host survival/migration behavior. Do not set it on your laptop or
Orca session. If cron is unavailable, give the same wrapper to RC's supported recurring
scheduler; do not improvise a long-lived sleep process.

## 6. Monitor and recover

Check status and private logs after the first jobs, then regularly and after site outages:

```bash
"$HOME/.local/opt/hpc-scratch-manager/.venv/bin/hpc-scratch-manager" \
  --config "$HOME/.config/hpc-scratch-manager/config.json" status
squeue -u "$USER"
```

Review `slurm-JOBID.log`, `trigger.log`, `refresh-report.jsonl`, and the summary's
`failed` / `unverified` counts. Use `sacct -j JOBID --format=JobID,State,ExitCode` when
accounting is available and RC permits it. `status` reports durable records and warns
about stale/partial/missing state; it does not query live Slurm or send notifications.
A saved `queued` record can remain after completion; the successful completion field
is the deciding evidence. Keep ASU's own storage notifications enabled. Agree with RC
on an independent monitoring method that can detect a missing daily trigger.

For exhausted retries, investigate failed files, resource limits, wrapper paths and
job output. For an uncertain submission, reconcile `squeue` **and** accounting with RC;
absence from one queue view does not prove no job exists. Cancel any surviving project
job by its exact ID, wait for it to stop, and only then explicitly reset:

```bash
"$HOME/.local/opt/hpc-scratch-manager/.venv/bin/hpc-scratch-manager" \
  --config "$HOME/.config/hpc-scratch-manager/config.json" reset-retries
```

The reset revokes old job tokens but does not cancel jobs. The next daily trigger can
try again. A failed/unverified file changed since inventory needs a deliberate fresh
manual run without `--resume`; do not automatically drop failures from the selection.
Keep approval valid during recovery. See [troubleshooting](troubleshooting.md).

## 7. Uninstall without deleting research data or unrelated schedules

1. On the actual trigger host, run `crontab -e` and remove **only** the line ending in
   `# HSM_PROJECT_DAILY`. Preserve all other entries. For another scheduler, remove only
   its project trigger using RC's supported mechanism. Verify the trigger is gone.
2. Identify this installation's Slurm job using `status`'s saved ID and `squeue`. Cancel
   only its exact job IDs with `scancel JOBID`; never `scancel -u` or another bulk command.
   Wait for them to stop. Use accounting if necessary. Keep wrappers/state until resolved.
3. Set `scheduler_enabled=false` and `refresh_enabled=false` in the private config.
   With no active jobs, `reset-retries` also revokes pending wrapper tokens. Never unlink
   a live lock file. Stopping jobs does not reset any research-file timestamp.
4. Optionally archive the **private** state/reports for your records. Remove the project's
   private wrappers, config, virtual environment/clone and state using your file manager
   only after verifying their exact locations. This project deliberately supplies no
   recursive deletion command. Keep all selected scratch roots/research files intact.

No global Git setting, SSH configuration or unrelated cron entry needs changing.
