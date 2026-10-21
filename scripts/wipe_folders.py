"""Wipe Gmail Promotions, Updates, and Spam.

Promotions/Updates are categories (not real IMAP folders): search All Mail and
move matches to Trash. Spam is the real Junk folder: permanently delete.

Default is dry-run. Pass --apply to delete. With --apply, Trash is emptied at
the end unless --keep-trash is set (so storage can actually free up).
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
from scripts.lib.paths import REPO_ROOT, wipe_log_path  # noqa: E402

# Gmail category queries (All Mail) + real Spam folder.
DEFAULT_TARGETS = ("promotions", "updates", "spam")


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _append_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _wipe_category(
    gmail: GmailSession,
    *,
    name: str,
    query: str,
    apply: bool,
    batch_size: int,
    delay: float,
    log_path: Path,
) -> int:
    print(f"  [{name}] searching: {query}", flush=True)
    # Ensure All Mail selected for category searches.
    if gmail.all_mail_folder and gmail.folder != gmail.all_mail_folder:
        gmail.select(gmail.all_mail_folder, readonly=False)
    uids = gmail.search_raw(query)
    print(f"  [{name}] matched {len(uids)}", flush=True)
    if not uids:
        _append_log(
            log_path,
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "target": name,
                "status": "empty",
                "count": 0,
            },
        )
        return 0
    if not apply:
        print(f"  [{name}] WOULD move {len(uids)} → Trash", flush=True)
        return len(uids)

    moved = 0
    for index, batch in enumerate(chunks(uids, batch_size), start=1):
        gmail.move_to_trash(batch)
        moved += len(batch)
        print(f"  [{name}] trashed {moved}/{len(uids)}", flush=True)
        if delay > 0:
            time.sleep(delay)
    _append_log(
        log_path,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "target": name,
            "status": "ok",
            "action": "trash",
            "count": moved,
        },
    )
    return moved


def _wipe_spam(
    gmail: GmailSession,
    *,
    apply: bool,
    batch_size: int,
    delay: float,
    log_path: Path,
) -> int:
    spam = gmail.spam_folder
    if not spam:
        print("  [spam] no Junk folder found", flush=True)
        return 0
    count = gmail.select(spam, readonly=not apply)
    uids = gmail.search_all_uids()
    print(f"  [spam] folder={spam} exists≈{count} uids={len(uids)}", flush=True)
    if not uids:
        _append_log(
            log_path,
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "target": "spam",
                "status": "empty",
                "count": 0,
            },
        )
        return 0
    if not apply:
        print(f"  [spam] WOULD permanently delete {len(uids)}", flush=True)
        return len(uids)

    deleted = 0
    for batch in chunks(uids, batch_size):
        deleted += gmail.delete_permanent(batch)
        print(f"  [spam] deleted {deleted}/{len(uids)}", flush=True)
        if delay > 0:
            time.sleep(delay)
    _append_log(
        log_path,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "target": "spam",
            "status": "ok",
            "action": "permanent_delete",
            "count": deleted,
        },
    )
    if gmail.all_mail_folder:
        gmail.select(gmail.all_mail_folder, readonly=False)
    return deleted


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/wipe_folders.py\n"
            "  python scripts/wipe_folders.py --apply\n"
            "  python scripts/wipe_folders.py --apply --target spam --target promotions\n"
        ),
    )
    parser.add_argument("--mailbox", default=None, help="Slug, email, or all")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually wipe (trash categories / delete spam)",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        choices=list(DEFAULT_TARGETS),
        help="Only these targets (repeatable). Default: promotions, updates, spam",
    )
    parser.add_argument(
        "--keep-trash",
        action="store_true",
        help="Do not empty Trash after wipe (default with --apply: empty Trash)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="IMAP batch size (default: 200)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Pause between batches (default: 0.05s)",
    )
    args = parser.parse_args()

    targets = tuple(args.targets) if args.targets else DEFAULT_TARGETS

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
    print(f"Mode: {mode} targets={','.join(targets)}")

    for account in accounts:
        log_path = wipe_log_path(account.slug)
        print(f"\nMailbox {account.address} ({account.slug})")
        try:
            with GmailSession(
                account.address, account.password, readonly=not args.apply
            ) as gmail:
                print(
                    f"  All Mail={gmail.exists} Trash={gmail.trash_folder} "
                    f"Spam={gmail.spam_folder}",
                    flush=True,
                )
                totals: dict[str, int] = {}
                if "promotions" in targets:
                    totals["promotions"] = _wipe_category(
                        gmail,
                        name="promotions",
                        query="category:promotions",
                        apply=args.apply,
                        batch_size=args.batch_size,
                        delay=args.delay,
                        log_path=log_path,
                    )
                if "updates" in targets:
                    totals["updates"] = _wipe_category(
                        gmail,
                        name="updates",
                        query="category:updates",
                        apply=args.apply,
                        batch_size=args.batch_size,
                        delay=args.delay,
                        log_path=log_path,
                    )
                if "spam" in targets:
                    totals["spam"] = _wipe_spam(
                        gmail,
                        apply=args.apply,
                        batch_size=args.batch_size,
                        delay=args.delay,
                        log_path=log_path,
                    )

                if args.apply and not args.keep_trash:
                    print("  [trash] emptying…", flush=True)
                    emptied = gmail.empty_trash()
                    print(f"  [trash] permanently deleted {emptied}", flush=True)
                    _append_log(
                        log_path,
                        {
                            "at": datetime.now(timezone.utc).isoformat(),
                            "target": "trash",
                            "status": "ok",
                            "action": "empty",
                            "count": emptied,
                        },
                    )

                print(
                    "  Summary: "
                    + ", ".join(f"{k}={v}" for k, v in totals.items())
                )
                if args.apply:
                    print(f"  Log: {log_path.relative_to(REPO_ROOT)}")
        except LoginError:
            print(f"error: login failed for {account.address}", file=sys.stderr)
            return 1

    if not args.apply:
        print("\nDry run only. Re-run with --apply to wipe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
