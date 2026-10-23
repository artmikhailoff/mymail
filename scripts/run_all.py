"""Alias for `python scripts/mymail.py daily` (kept for old habits).

Old flags map onto the new pipeline: --fresh -> --full, --skip-scan is ignored
(sync is incremental and takes seconds), --skip-unsubscribe/--skip-delete/
--skip-wipe/--safe/--skip-download/--skip-email are passed through.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailbox")
    parser.add_argument("--since-year", type=int)
    parser.add_argument("--until-year", type=int)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--skip-scan", action="store_true")
    passthrough = (
        "--safe", "--skip-download", "--skip-unsubscribe", "--skip-delete", "--skip-wipe",
        "--skip-email", "--include-accounts", "--include-other",
    )
    for flag in passthrough:
        parser.add_argument(flag, action="store_true")
    args = parser.parse_args()

    cmd = [sys.executable, "-u", str(SCRIPTS / "mymail.py"), "daily"]
    if args.mailbox:
        cmd += ["--mailbox", args.mailbox]
    if args.fresh:
        cmd.append("--full")
    if args.since_year:
        cmd += ["--since-year", str(args.since_year)]
    if args.until_year:
        cmd += ["--until-year", str(args.until_year)]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    for flag in passthrough:
        if getattr(args, flag.lstrip("-").replace("-", "_")):
            cmd.append(flag)
    return subprocess.call(cmd, cwd=SCRIPTS.parent)


if __name__ == "__main__":
    raise SystemExit(main())
