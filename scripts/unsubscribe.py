"""Unsubscribe from commercial / noise mail using List-Unsubscribe.

Only messages classified as topic=noise (ads, promotions, recommendations).
One attempt per sender. Default is dry-run; pass --apply to actually unsubscribe.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.gmail import GmailSession  # noqa: E402
from scripts.lib.paths import REPO_ROOT, index_path, unsubscribe_log_path  # noqa: E402
from scripts.lib.stats import load_index, sender_key  # noqa: E402
from scripts.lib.unsubscribe import (  # noqa: E402
    UnsubscribeResult,
    append_log,
    display_from,
    load_done_senders,
    noise_candidates,
    parse_list_unsubscribe,
    perform_unsubscribe,
)


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
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/unsubscribe.py\n"
            "  python scripts/unsubscribe.py --limit 20\n"
            "  python scripts/unsubscribe.py --apply --limit 20\n"
            "  python scripts/unsubscribe.py --apply --sender shein.com\n"
        ),
    )
    parser.add_argument("--mailbox", default=None, help="Slug, email, or all")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually send one-click / mailto / GET unsubscribe requests",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N distinct noise senders (0 = all)",
    )
    parser.add_argument(
        "--sender",
        action="append",
        dest="senders",
        help="Only this sender email or domain substring (repeatable)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retry senders already logged as ok/no_header",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to sleep between apply actions (default: 1)",
    )
    args = parser.parse_args()

    if args.limit < 0:
        print("error: --limit must be 0 or greater", file=sys.stderr)
        return 1

    try:
        accounts = resolve_accounts(args.mailbox)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        from imapclient.exceptions import LoginError
    except ImportError:
        print(
            "error: imapclient is not installed. Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")

    for account in accounts:
        idx = index_path(account.slug)
        log_path = unsubscribe_log_path(account.slug)
        records = load_index(idx)
        if not records:
            print(f"skip {account.slug}: no index")
            continue

        candidates = noise_candidates(records)
        if args.senders:
            needles = [s.lower() for s in args.senders]
            candidates = [
                row
                for row in candidates
                if any(n in display_from(row).lower() for n in needles)
            ]

        done = set() if args.force else load_done_senders(log_path)
        pending: list[dict] = []
        skipped_done = 0
        for row in candidates:
            sender = sender_key(str(row.get("from") or ""))
            if sender in done:
                skipped_done += 1
                continue
            pending.append(row)
            if args.limit and len(pending) >= args.limit:
                break

        print(f"\nMailbox {account.address} ({account.slug})")
        print(f"Noise senders in index: {len(candidates)}")
        if skipped_done:
            print(f"Already handled (log): {skipped_done}")
        print(f"This run: {len(pending)}")
        if not pending:
            continue

        stats = {"ok": 0, "planned": 0, "no_header": 0, "error": 0, "skipped": 0}
        try:
            with GmailSession(account.address, account.password) as gmail:
                print(f"Connected (read-only IMAP for headers)")
                for index, record in enumerate(pending, start=1):
                    sender = display_from(record)
                    msgid = str(record.get("gmail_msgid") or "")
                    subject = str(record.get("subject") or "")
                    prefix = f"[{index}/{len(pending)}] {sender}"

                    if not msgid.isdigit():
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="error",
                            method="none",
                            detail="missing gmail_msgid",
                        )
                        stats["error"] += 1
                        print(f"{prefix}: ERROR {result.detail}")
                        if args.apply:
                            append_log(result, log_path)
                        continue

                    try:
                        uids = gmail.search_gm_msgid(msgid)
                    except Exception as exc:  # noqa: BLE001
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="error",
                            method="none",
                            detail=f"search failed: {exc}",
                        )
                        stats["error"] += 1
                        print(f"{prefix}: ERROR {result.detail}")
                        if args.apply:
                            append_log(result, log_path)
                        continue

                    if not uids:
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="error",
                            method="none",
                            detail="message not found in All Mail",
                        )
                        stats["error"] += 1
                        print(f"{prefix}: ERROR {result.detail}")
                        if args.apply:
                            append_log(result, log_path)
                        continue

                    try:
                        header_bytes = gmail.fetch_headers(uids[0])
                        target = parse_list_unsubscribe(header_bytes)
                    except Exception as exc:  # noqa: BLE001
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="error",
                            method="none",
                            detail=f"header fetch failed: {exc}",
                        )
                        stats["error"] += 1
                        print(f"{prefix}: ERROR {result.detail}")
                        if args.apply:
                            append_log(result, log_path)
                        continue

                    if target.method == "none":
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="no_header",
                            method="none",
                            detail=target.summarize(),
                        )
                        stats["no_header"] += 1
                        print(f"{prefix}: no List-Unsubscribe | {subject[:80]}")
                        if args.apply:
                            append_log(result, log_path)
                        continue

                    if not args.apply:
                        result = UnsubscribeResult(
                            sender=sender,
                            gmail_msgid=msgid,
                            subject=subject,
                            status="planned",
                            method=target.method,
                            target=target.value,
                            detail=target.summarize(),
                        )
                        stats["planned"] += 1
                        print(f"{prefix}: WOULD {target.summarize()}")
                        print(f"         {subject[:100]}")
                        continue

                    status, detail = perform_unsubscribe(
                        target,
                        from_address=account.address,
                        app_password=account.password,
                    )
                    result = UnsubscribeResult(
                        sender=sender,
                        gmail_msgid=msgid,
                        subject=subject,
                        status=status,
                        method=target.method,
                        target=target.value,
                        detail=detail,
                    )
                    key = status if status in stats else "error"
                    stats[key] += 1
                    print(f"{prefix}: {status.upper()} {detail}")
                    append_log(result, log_path)
                    if args.delay > 0:
                        time.sleep(args.delay)
        except LoginError:
            print(
                f"error: Gmail rejected login for {account.address}",
                file=sys.stderr,
            )
            return 1
        except KeyboardInterrupt:
            print("\nStopped.", file=sys.stderr)
            return 130

        print(
            "Summary: "
            + ", ".join(f"{key}={count}" for key, count in stats.items() if count)
        )
        if args.apply:
            print(f"Log: {log_path.relative_to(REPO_ROOT)}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to unsubscribe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
