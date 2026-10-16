#!/bin/bash
# Copy OUTSIDE the repository; replace both absolute paths. Run only on an RC-approved host.
set -euo pipefail
umask 077
cd /
exec /home/REPLACE_WITH_USER/.local/opt/hpc-scratch-manager/.venv/bin/python \
    -m hpc_scratch_manager \
    --config /home/REPLACE_WITH_USER/.config/hpc-scratch-manager/config.json trigger
