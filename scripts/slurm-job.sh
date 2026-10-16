#!/bin/bash
# Copy OUTSIDE the repository and replace the Python path before use.
# Resource requests belong in private sbatch_args; no partition/account is assumed here.
set -euo pipefail
umask 077
if [ "$#" -ne 2 ]; then
    echo 'Usage: slurm-job.sh ABSOLUTE_CONFIG SUBMISSION_TOKEN' >&2
    exit 1
fi
cd /
exec /home/REPLACE_WITH_USER/.local/opt/hpc-scratch-manager/.venv/bin/python \
    -m hpc_scratch_manager --config "$1" scheduled-run --token "$2"
