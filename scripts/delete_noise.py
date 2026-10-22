"""Move marketing / noise mail to Gmail Trash.

Only topic=noise from the census index. Default is dry-run; pass --apply to
move messages. Gmail Trash is recoverable for ~30 days.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.gmail import GmailSession, chunks  # noqa: E402
from scripts.lib import db  # noqa: E402
from scripts.lib.paths import REPO_ROOT, delete_log_path, index_path  # noqa: E402
from scripts.lib.stats import load_index, sender_key  # noqa: E402


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _load_deleted(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") in ("ok", "missing"):
            msgid = str(row.get("gmail_msgid") or "")
            if msgid.isdigit():
                done.add(msgid)
    return done


def _append_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/delete_noise.py --limit 50\n"
            "  python scripts/delete_noise.py --apply --limit 50\n"
            "  python scripts/delete_noise.py --apply --sender shein.com\n"
        ),
    )
    parser.add_argument("--mailbox", default=None, help="Slug, email, or all")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move noise messages to Trash",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="At most N messages per mailbox (0 = all)",
    )
    parser.add_argument(
        "--sender",
        action="append",
        dest="senders",
        help="Only this sender email/domain substring (repeatable)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retry msgids already logged as deleted",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="IMAP move batch size (default: 50)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Pause between batches when applying (default: 0.2s)",
    )
    args = parser.parse_args()

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

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode} (noise → Gmail Trash)")

    grand = {"moved": 0, "planned": 0, "missing": 0, "errors": 0}

    for account in accounts:
        records = load_index(index_path(account.slug))
        noise = [row for row in records if str(row.get("topic") or "") == "noise"]
        junk = db.junk_msgids(account.slug)
        if junk is not None:
            noise = [row for row in noise if str(row.get("gmail_msgid")) in junk]
        if args.senders:
            needles = [s.lower() for s in args.senders]
            noise = [
                row
                for row in noise
                if any(n in sender_key(str(row.get("from") or "")).lower() for n in needles)
                or any(n in str(row.get("from") or "").lower() for n in needles)
            ]

        log_path = delete_log_path(account.slug)
        already = set() if args.force else _load_deleted(log_path)
        pending = [
            row
            for row in noise
            if str(row.get("gmail_msgid") or "").isdigit()
            and str(row["gmail_msgid"]) not in already
        ]
        pending.sort(key=lambda row: str(row.get("internal_date") or ""), reverse=True)
        if args.limit:
            pending = pending[: args.limit]

        print(
            f"\nMailbox {account.address}: {len(noise)} noise in index, "
            f"{len(pending)} this run"
            + (f" (skipped logged {len(already)})" if already and not args.force else "")
        )
        if not pending:
            continue

        if not args.apply:
            for row in pending[:15]:
                print(
                    f"  WOULD trash | {row.get('internal_date')} | "
                    f"{row.get('from')} | {row.get('subject')}"
                )
            if len(pending) > 15:
                print(f"  … {len(pending) - 15} more")
            grand["planned"] += len(pending)
            continue

        try:
            with GmailSession(
                account.address, account.password, readonly=False
            ) as gmail:
                print(
                    f"  Connected writable → Trash={gmail.trash_folder} "
                    f"(All Mail={gmail.exists})"
                )
                # Resolve UIDs in batches of msgid lookups is slow; do per-message
                # search then move in chunks of resolved UIDs.
                uid_rows: list[tuple[int, dict]] = []
                for index, row in enumerate(pending, start=1):
                    msgid = str(row["gmail_msgid"])
                    if index == 1 or index % 100 == 0 or index == len(pending):
                        print(
                            f"  resolve uids {index}/{len(pending)} "
                            f"(found={len(uid_rows)})",
                            flush=True,
                        )
                    try:
                        uids = gmail.search_gm_msgid(msgid)
                    except Exception as exc:  # noqa: BLE001
                        grand["errors"] += 1
                        _append_log(
                            log_path,
                            {
                                "at": datetime.now(timezone.utc).isoformat(),
                                "gmail_msgid": msgid,
                                "status": "error",
                                "detail": f"search: {exc}",
                                "from": row.get("from"),
                                "subject": row.get("subject"),
                            },
                        )
                        continue
                    if not uids:
                        grand["missing"] += 1
                        _append_log(
                            log_path,
                            {
                                "at": datetime.now(timezone.utc).isoformat(),
                                "gmail_msgid": msgid,
                                "status": "missing",
                                "detail": "not in All Mail",
                                "from": row.get("from"),
                                "subject": row.get("subject"),
                            },
                        )
                        continue
                    uid_rows.append((uids[0], row))

                moved = 0
                for batch in chunks(uid_rows, max(1, args.batch_size)):
                    uids = [uid for uid, _ in batch]
                    try:
                        gmail.move_to_trash(uids)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  batch move failed: {exc}", file=sys.stderr)
                        grand["errors"] += len(batch)
                        for uid, row in batch:
                            _append_log(
                                log_path,
                                {
                                    "at": datetime.now(timezone.utc).isoformat(),
                                    "gmail_msgid": row.get("gmail_msgid"),
                                    "status": "error",
                                    "detail": str(exc),
                                    "from": row.get("from"),
                                    "subject": row.get("subject"),
                                },
                            )
                        continue
                    for uid, row in batch:
                        moved += 1
                        _append_log(
                            log_path,
                            {
                                "at": datetime.now(timezone.utc).isoformat(),
                                "gmail_msgid": row.get("gmail_msgid"),
                                "status": "ok",
                                "detail": f"moved uid {uid} to Trash",
                                "from": row.get("from"),
                                "subject": row.get("subject"),
                            },
                        )
                    print(f"  trashed {moved}/{len(uid_rows)}")
                    if args.delay > 0:
                        time.sleep(args.delay)
                grand["moved"] += moved
                print(
                    f"  Log: {log_path.relative_to(REPO_ROOT)} "
                    f"(moved={moved}, missing={grand['missing']})"
                )
        except LoginError:
            print(f"error: login failed for {account.address}", file=sys.stderr)
            return 1

    print(
        "\nSummary: "
        + ", ".join(f"{k}={v}" for k, v in grand.items() if v)
    )
    if not args.apply:
        print("Dry run only. Re-run with --apply to move noise to Trash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
