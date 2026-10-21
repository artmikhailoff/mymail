"""Single entry point: scan → archive signal topics → clean noise.

Default pipeline:
  1. scan_mailbox   (headers + classify)
  2. download_bodies (signal topics only)
  3. extract_text
  4. build_bundles
  5. unsubscribe --apply
  6. delete_noise --apply  (classified noise → Trash)
  7. wipe_folders --apply  (Promotions + Updates → Trash, Spam delete, empty Trash)
  8. report

Examples:
  python scripts/run_all.py
  python scripts/run_all.py --since-year 2024
  python scripts/run_all.py --safe              # no unsubscribe / trash
  python scripts/run_all.py --mailbox all
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, "-u", str(ROOT / "scripts" / script), *(extra or [])]
    print(f"\n==> {' '.join(cmd)}", flush=True)
    completed = subprocess.run(cmd, cwd=str(ROOT))
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Slug, email, or all (default: all configured accounts)",
    )
    parser.add_argument(
        "--since-year",
        type=int,
        default=datetime.now().year - 4,
        help="First calendar year to scan (default: five calendar years)",
    )
    parser.add_argument(
        "--until-year",
        type=int,
        default=datetime.now().year,
        help="Last calendar year to scan (default: this year)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap new scan/download items this run (0 = all)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Rewrite the census index from this scan",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Skip unsubscribe, delete_noise, and wipe_folders (archive only)",
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Use existing index; do not call Gmail for census",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip body download / extract / bundles",
    )
    parser.add_argument(
        "--skip-unsubscribe",
        action="store_true",
        help="Skip List-Unsubscribe step",
    )
    parser.add_argument(
        "--skip-delete",
        action="store_true",
        help="Skip moving classified noise to Trash",
    )
    parser.add_argument(
        "--skip-wipe",
        action="store_true",
        help="Skip wiping Promotions / Updates / Spam",
    )
    parser.add_argument(
        "--include-accounts",
        action="store_true",
        help="Also download topic=accounts",
    )
    parser.add_argument(
        "--include-other",
        action="store_true",
        help="Also download topic=other",
    )
    args = parser.parse_args()

    mailbox_args: list[str] = []
    if args.mailbox:
        mailbox_args = [f"--mailbox={args.mailbox}"]

    if not args.skip_scan:
        scan_args = [
            *mailbox_args,
            f"--since-year={args.since_year}",
            f"--until-year={args.until_year}",
        ]
        if args.limit > 0:
            scan_args.append(f"--limit={args.limit}")
        if args.fresh:
            scan_args.append("--fresh")
        code = _run("scan_mailbox.py", scan_args)
        if code != 0:
            return code

    if not args.skip_download:
        dl_args = list(mailbox_args)
        if args.limit > 0:
            dl_args.append(f"--limit={args.limit}")
        if args.include_accounts:
            dl_args.append("--include-accounts")
        if args.include_other:
            dl_args.append("--include-other")
        code = _run("download_bodies.py", dl_args)
        if code != 0:
            return code

        code = _run("extract_text.py", mailbox_args)
        if code != 0:
            return code

        code = _run("build_bundles.py", mailbox_args)
        if code != 0:
            return code

    if not args.safe and not args.skip_unsubscribe:
        code = _run("unsubscribe.py", [*mailbox_args, "--apply"])
        if code != 0:
            return code

    if not args.safe and not args.skip_delete:
        code = _run("delete_noise.py", [*mailbox_args, "--apply"])
        if code != 0:
            return code

    if not args.safe and not args.skip_wipe:
        code = _run("wipe_folders.py", [*mailbox_args, "--apply"])
        if code != 0:
            return code

    code = _run("report.py", [*mailbox_args, "--rebuild"])
    if code != 0:
        return code

    print("\nDone. Point an agent at data/bundles/<slug>/<topic>.md "
          "or data/processed/<slug>/<topic>/<year>/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
