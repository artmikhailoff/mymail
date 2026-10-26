"""Scan Gmail (read-only) and classify messages into topics.

Fetches headers only, writes data/index.jsonl and data/stats.json.
The mailbox is never marked read, moved, or deleted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.classify import classify_record  # noqa: E402
from scripts.lib.gmail import (  # noqa: E402
    DEFAULT_SINCE_YEAR,
    META_FIELDS,
    GmailSession,
    chunks,
    describe,
    envelope_record,
    search_year,
    uid_map,
    years_to_scan,
)
from scripts.lib.paths import REPO_ROOT, index_path, stats_path  # noqa: E402
from scripts.lib.stats import aggregate, format_report, load_index, write_stats  # noqa: E402


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _load_known_msgids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    known: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        msgid = str(row.get("gmail_msgid") or "")
        if msgid.isdigit():
            known.add(msgid)
    return known


def _append_index(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _rewrite_index(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _fetch_classified(
    gmail: GmailSession,
    uids: list[int],
    known: set[str],
    *,
    limit: int,
) -> tuple[list[dict], int, int]:
    """Fetch headers, classify, skip already-indexed msgids.

    Returns (new_records, skipped_known, fetch_errors).
    """
    pending_uids = sorted(uids, reverse=True)
    records: list[dict] = []
    skipped = 0
    errors = 0

    for index, chunk in enumerate(chunks(pending_uids, 100), start=1):
        if limit and len(records) >= limit:
            break
        fetched = uid_map(gmail.fetch(chunk, META_FIELDS))
        if index == 1 or index % 10 == 0:
            print(f"  headers {min(index * 100, len(pending_uids))}/{len(pending_uids)}")
        for uid in chunk:
            if limit and len(records) >= limit:
                break
            item = fetched.get(uid)
            if not item:
                errors += 1
                continue
            try:
                record = envelope_record(item)
            except RuntimeError as exc:
                errors += 1
                print(f"  skip uid {uid}: {exc}", file=sys.stderr)
                continue
            msgid = record["gmail_msgid"]
            if msgid in known:
                skipped += 1
                continue
            result = classify_record(record)
            record["topic"] = result.topic
            record["reasons"] = list(result.reasons)
            record["confidence"] = result.confidence
            records.append(record)
            known.add(msgid)
    return records, skipped, errors


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/scan_mailbox.py --check\n"
            "  python scripts/scan_mailbox.py --dry-run --since-year 2025\n"
            "  python scripts/scan_mailbox.py --limit 1000\n"
            "  python scripts/scan_mailbox.py --fresh\n"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Log in and print the All Mail folder size, then stop",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and print a report; do not write index/stats",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Classify at most N new messages this run, newest first (0 = all)",
    )
    parser.add_argument(
        "--since-year",
        type=int,
        default=DEFAULT_SINCE_YEAR,
        help="First calendar year to search, inclusive (default: 2008)",
    )
    parser.add_argument(
        "--until-year",
        type=int,
        default=datetime.now().year,
        help="Last calendar year to search, inclusive (default: this year)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore existing index and rewrite it from this scan",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="Print N sample rows per topic after the scan (default: 5)",
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Mailbox slug, email, or 'all' (default: all configured)",
    )
    args = parser.parse_args()

    if args.limit < 0:
        print("error: --limit must be 0 or greater", file=sys.stderr)
        return 1

    try:
        accounts = resolve_accounts(args.mailbox)
        years = years_to_scan(args.since_year, args.until_year)
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

    try:
        for account in accounts:
            idx = index_path(account.slug)
            st = stats_path(account.slug)
            with GmailSession(account.address, account.password) as gmail:
                print(f"Connected as {account.address} ({account.slug})")
                print(f"Folder: {gmail.folder} ({gmail.exists} messages, read-only)")
                if args.check:
                    continue

                known: set[str] = set() if args.fresh else _load_known_msgids(idx)
                if known and not args.fresh:
                    print(f"Already indexed: {len(known)}")

                all_new: list[dict] = []
                remaining = args.limit
                for year in reversed(years):
                    if args.limit and remaining <= 0:
                        break
                    print(f"Searching {year}…")
                    try:
                        uids, split = search_year(gmail, year)
                    except LoginError:
                        raise
                    except Exception as exc:
                        print(f"  search failed: {exc}", file=sys.stderr)
                        continue
                    note = " (split by month)" if split else ""
                    print(f"  {len(uids)} messages{note}")
                    year_limit = remaining if args.limit else 0
                    records, skipped, errors = _fetch_classified(
                        gmail, uids, known, limit=year_limit
                    )
                    for row in records:
                        row["mailbox"] = account.slug
                    print(
                        f"  new={len(records)} skipped_known={skipped} errors={errors}"
                    )
                    all_new.extend(records)
                    if args.limit:
                        remaining = max(0, args.limit - len(all_new))

                print(f"Classified this run: {len(all_new)}")
                if not all_new and not known:
                    print("No messages found.")
                    continue

                if args.dry_run:
                    stats = aggregate(all_new)
                    print(format_report(stats))
                    _print_samples(all_new, args.sample)
                    print("Dry run: nothing written.")
                    continue

                if args.fresh:
                    _rewrite_index(idx, all_new)
                    indexed = all_new
                else:
                    _append_index(idx, all_new)
                    indexed = load_index(idx)

                stats = aggregate(indexed)
                write_stats(stats, st)
                print(format_report(stats))
                _print_samples(all_new or indexed, args.sample)
                print(
                    f"Wrote {idx.relative_to(REPO_ROOT)} "
                    f"and {st.relative_to(REPO_ROOT)}"
                )
        return 0
    except LoginError:
        print(
            "error: Gmail rejected the login. Use an app password (Google Account → "
            "Security → App passwords) and enable IMAP in Gmail settings.",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Index rows already written are kept.", file=sys.stderr)
        return 130


def _print_samples(records: list[dict], per_topic: int) -> None:
    if per_topic <= 0 or not records:
        return
    from collections import defaultdict

    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        topic = str(record.get("topic") or "other")
        if len(buckets[topic]) < per_topic:
            buckets[topic].append(record)
    print("Samples:")
    for topic in sorted(buckets):
        print(f"  [{topic}]")
        for record in buckets[topic]:
            print(f"    {describe(record)}")
            reasons = record.get("reasons") or []
            if reasons:
                print(f"      {'; '.join(reasons)}")


if __name__ == "__main__":
    raise SystemExit(main())
