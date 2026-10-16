# Contributing

Use synthetic temporary data only. Never run development tests on cluster research
files or require cluster access, tokens, SSH keys or secrets in CI.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m unittest discover -s tests -v
bash -n scripts/daily-trigger.sh scripts/slurm-job.sh
```

The suite tests metadata-only audit behavior, filenames with raw bytes/newlines,
exclusions, symlink/root escape and substitution, mount boundaries, special files,
private config, owner checks, cooperative limits, interruption and checkpoint recovery,
mtime/content preservation, empty files, simulated noatime and partial failures,
bounded workers, locking, exact elapsed-day calculation, Slurm duplicates, submission
ambiguity, token revocation, retry limits and state-only scheduling.

Some filesystem observations are Linux-specific. Tests exercise a real `/proc` mount
boundary and temporary files, plus injected failures for unprivileged reproducibility;
they do not establish Sol's filesystem behavior or purge criteria. CI is Linux Python
3.10/3.12/3.13. No test mounts a filesystem or needs root. No runtime dependency is added.

Before committing, inspect the entire staged diff and new files. Keep real settings,
private paths, research filenames, logs and credentials outside the clone. Use a GitHub
noreply commit identity if available, set only per invocation/repository, never globally.
Do not attach unsanitized runtime reports to public issues. An example must remain
explicit placeholders or synthetic data. Public examples must leave refresh/scheduling
disabled. Policy changes need current official citations and a clear distinction between
documented rules, operator attestations and unresolved implementation assumptions.
