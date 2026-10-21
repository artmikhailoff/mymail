"""Download full .eml for allowlisted topics into data/raw/<mailbox>/<topic>/<year>/.

Never downloads noise. Uses catalog index.jsonl from scan_mailbox.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.gmail import GmailSession  # noqa: E402
from scripts.lib.paths import (  # noqa: E402
    DOWNLOAD_TOPICS,
    REPO_ROOT,
    index_path,
    raw_message_path,
    year_of_record,
)
from scripts.lib.stats import load_index  # noqa: E402
from scripts.lib.store import save_raw_message  # noqa: E402


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Mailbox slug, email, or 'all' (default: all configured)",
    )
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="Only these topics (repeatable). Default: download allowlist",
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
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Download at most N new messages per mailbox (0 = all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be downloaded; write nothing",
    )
    args = parser.parse_args()

    wanted = set(args.topics or DOWNLOAD_TOPICS)
    if args.include_accounts:
        wanted.add("accounts")
    if args.include_other:
        wanted.add("other")
    wanted.discard("noise")  # hard rule
    if not wanted:
        print("error: no topics left to download", file=sys.stderr)
        return 1

    try:
        accounts = resolve_accounts(args.mailbox)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        from imapclient.exceptions import LoginError
    except ImportError:
        print("error: pip install -r requirements.txt", file=sys.stderr)
        return 1

    total_saved = 0
    for account in accounts:
        path = index_path(account.slug)
        records = load_index(path)
        pending = [
            row
            for row in records
            if str(row.get("topic") or "") in wanted
            and str(row.get("gmail_msgid") or "").isdigit()
            and not raw_message_path(
                account.slug,
                str(row.get("topic") or "other"),
                year_of_record(row),
                str(row["gmail_msgid"]),
            ).is_file()
        ]
        pending.sort(key=lambda row: str(row.get("internal_date") or ""), reverse=True)
        if args.limit:
            pending = pending[: args.limit]

        print(
            f"Mailbox {account.address} ({account.slug}): "
            f"{len(pending)} to download "
            f"(topics={','.join(sorted(wanted))})"
        )
        if args.dry_run:
            for row in pending[:20]:
                print(
                    f"  WOULD {row.get('topic')} {year_of_record(row)} "
                    f"| {row.get('from')} | {row.get('subject')}"
                )
            if len(pending) > 20:
                print(f"  … {len(pending) - 20} more")
            continue

        if not pending:
            continue

        try:
            with GmailSession(account.address, account.password) as gmail:
                print(f"  Connected ({gmail.exists} in All Mail)")
                for index, row in enumerate(pending, start=1):
                    msgid = str(row["gmail_msgid"])
                    try:
                        uids = gmail.search_gm_msgid(msgid)
                        if not uids:
                            print(f"  missing {msgid}", file=sys.stderr)
                            continue
                        body = gmail.fetch_body(uids[0])
                        out = save_raw_message(slug=account.slug, record=row, body=body)
                        total_saved += 1
                        rel = out.relative_to(REPO_ROOT)
                        print(f"  [{index}/{len(pending)}] {row.get('topic')} → {rel}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"  skip {msgid}: {exc}", file=sys.stderr)
        except LoginError:
            print(f"error: login failed for {account.address}", file=sys.stderr)
            return 1

    print(f"Saved {total_saved} message(s) under data/raw/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
