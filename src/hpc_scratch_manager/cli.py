import argparse
import json
import signal
import sqlite3
import subprocess
import sys

from .config import load
from .engine import run
from .probe import probe
from .scheduler import reset_retries, trigger
from .state import status


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Metadata-first scratch manager; refresh is disabled by default")
    parser.add_argument("--config", help="private mode-600 JSON file outside repository/scratch")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("audit", "refresh"):
        p = sub.add_parser(name)
        p.add_argument("--resume", action="store_true", help="resume matching unfinished checkpoint")
        if name == "refresh":
            p.add_argument("--execute", action="store_true", help="requires recorded site approval and confirmed behavior")
    p = sub.add_parser("probe")
    p.add_argument("--test-dir", required=True, help="explicit disposable-test parent on target filesystem")
    p.add_argument("--output-dir", required=True, help="private persistent output directory")
    sub.add_parser("status")
    sub.add_parser("trigger")
    sub.add_parser("reset-retries", help="only after reconciling/cancelling jobs in squeue and sacct")
    p = sub.add_parser("scheduled-run", help="internal Slurm entrypoint; requires a submission token")
    p.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "probe":
            result, code = probe(args.test_dir, args.output_dir), 0
        else:
            if not args.config:
                parser.error("--config is required for this command")
            c = load(args.config)
            if args.command in ("audit", "refresh", "scheduled-run"):
                scheduled = args.command == "scheduled-run"
                result, code = run(c, args.command, execute=scheduled or getattr(args, "execute", False),
                                   resume=None if scheduled else args.resume,
                                   token=args.token if scheduled else None)
            elif args.command == "status":
                result, code = status(c), 0
            elif args.command == "trigger":
                result, code = trigger(c), 0
            else:
                result, code = reset_retries(c), 0
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return code
    except KeyboardInterrupt:
        print("interrupted; unfinished checkpoints may be resumed", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
