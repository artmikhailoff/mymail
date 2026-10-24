"""mymail — one CLI for the whole pipeline.

  daily     sync -> reclassify -> download -> text -> extract -> triage -> inbox -> clean
            -> reconcile -> kb -> digest -> email digests
  reconcile mark catalogued messages that are no longer in Gmail as gone
  migrate   import legacy index.jsonl + already-downloaded files into data/store/mail.db
  sync      catalog new mail (incremental: only UIDs above the last sync)
  reclassify  re-run the topic classifier over the whole catalog (headers only)
  download  fetch bodies for signal topics not downloaded yet
  text      extract plain text for downloaded bodies
  extract   facts (transactions, bills, documents, contacts) + full-text index
  triage    recompute priority (action/fyi/archive/junk) from config/rules.toml
  inbox     label action/fyi and archive the rest of the Inbox (dry run without --apply)
  clean     unsubscribe + delete noise + wipe Promotions/Updates/Spam
  kb        regenerate data/kb/<mailbox>/*.md
  digest    write data/digests/<mailbox>/YYYY-MM-DD.md, week.md, and month.md
            and email them (--skip-email writes files only)
  ask       run read-only SQL against mail.db (see scripts/ask.py)

Every step is incremental and safe to rerun.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import db  # noqa: E402
from scripts.lib.accounts import Account, resolve_accounts  # noqa: E402
from scripts.lib.paths import REPO_ROOT, rel_path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            pass


def _log(message: str) -> None:
    print(message, flush=True)


def _step(title: str) -> float:
    _log(f"\n=== {title} ===")
    return time.monotonic()


def _accounts(selector: str | None) -> list[Account]:
    return resolve_accounts(selector)


def _needs_migration(conn, account: Account) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE mailbox = ?", (account.slug,)
    ).fetchone()
    return row[0] == 0


# ------------------------------------------------------------------ steps


def cmd_migrate(args, conn) -> int:
    from scripts.lib.pipeline import migrate_mailbox

    for account in _accounts(args.mailbox):
        _log(f"Mailbox {account.address}")
        migrate_mailbox(conn, account, log=_log)
    return 0


def cmd_sync(args, conn) -> int:
    from scripts.lib.pipeline import migrate_mailbox, sync_mailbox

    for account in _accounts(args.mailbox):
        _log(f"Mailbox {account.address}")
        if _needs_migration(conn, account):
            migrate_mailbox(conn, account, log=_log)
        sync_mailbox(
            conn,
            account,
            full=getattr(args, "full", False),
            since_year=getattr(args, "since_year", None),
            until_year=getattr(args, "until_year", None),
            limit=getattr(args, "limit", 0) or 0,
            log=_log,
        )
    return 0


def cmd_download(args, conn) -> int:
    from scripts.lib.pipeline import download_bodies, download_topics

    topics = download_topics(args.include_accounts, args.include_other)
    for account in _accounts(args.mailbox):
        _log(f"Mailbox {account.address}")
        download_bodies(
            conn,
            account,
            topics=topics,
            limit=getattr(args, "download_limit", 0) or 0,
            max_bytes=int((getattr(args, "max_mb", 0) or 0) * 1_000_000),
            dry_run=getattr(args, "dry_run", False),
            log=_log,
        )
    return 0


def cmd_text(args, conn) -> int:
    from scripts.lib.pipeline import extract_texts

    for account in _accounts(args.mailbox):
        extract_texts(conn, account.slug, force=getattr(args, "force", False), log=_log)
    return 0


def cmd_extract(args, conn) -> int:
    from scripts.lib.extract import run_extractors, summarize

    stats = run_extractors(conn, force=getattr(args, "force", False), log=_log)
    _log("  new/updated facts: " + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "none"))
    for line in summarize(conn):
        _log(line)
    return 0


def cmd_reconcile(args, conn) -> int:
    from scripts.lib.pipeline import reconcile_mailbox

    for account in _accounts(args.mailbox):
        _log(f"Mailbox {account.address}")
        reconcile_mailbox(conn, account, log=_log)
    return 0


def cmd_reclassify(args, conn) -> int:
    from scripts.lib.pipeline import reclassify_all

    changes = reclassify_all(conn)
    total = sum(changes.values())
    _log(f"  {total} topic change(s)" + (": " + ", ".join(f"{k}={v}" for k, v in sorted(changes.items(), key=lambda kv: -kv[1])) if changes else ""))
    return 0


def cmd_triage(args, conn) -> int:
    from scripts.lib.triage import triage_all

    counts = triage_all(conn)
    _log("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def cmd_inbox(args, conn) -> int:
    from scripts.lib.inbox import tidy_inbox

    for account in _accounts(args.mailbox):
        _log(f"Mailbox {account.address}")
        tidy_inbox(conn, account, apply=args.apply, log=_log)
    return 0


def cmd_clean(args, conn) -> int:
    base = [sys.executable, "-u"]
    mailbox = ["--mailbox", args.mailbox] if args.mailbox else []
    steps = []
    if not getattr(args, "skip_unsubscribe", False):
        steps.append(["unsubscribe.py", "--apply", *mailbox])
    if not getattr(args, "skip_delete", False):
        steps.append(["delete_noise.py", "--apply", *mailbox])
    if not getattr(args, "skip_wipe", False):
        steps.append(["wipe_folders.py", "--apply", *mailbox])
    status = 0
    for script, *rest in steps:
        _log(f"--- {script} {' '.join(rest)}")
        code = subprocess.call([*base, str(SCRIPTS / script), *rest], cwd=REPO_ROOT)
        if code != 0:
            _log(f"  {script} exited {code}; continuing")
            status = code
    return status


def cmd_kb(args, conn) -> int:
    from scripts.lib.kb import build_kb

    for account in _accounts(args.mailbox):
        for path in build_kb(conn, account.slug, account.address):
            _log(f"  wrote {rel_path(path)}")
    return 0


def _write_digests(args, conn) -> list[tuple[Account, tuple[Path, Path, Path]]]:
    from scripts.lib.digest import build_digest, build_month_digest, build_week_digest

    days = getattr(args, "days", None)
    written: list[tuple[Account, tuple[Path, Path, Path]]] = []
    for account in _accounts(args.mailbox):
        paths = (
            build_digest(conn, account.slug, account.address, days=days),
            build_week_digest(conn, account.slug, account.address),
            build_month_digest(conn, account.slug, account.address),
        )
        for path in paths:
            _log(f"  wrote {rel_path(path)}")
        written.append((account, paths))
    return written


def _email_digests(written: list[tuple[Account, tuple[Path, Path, Path]]]) -> None:
    from scripts.lib.mail_send import send_digest_email

    errors: list[str] = []
    for account, (daily_path, week_path, month_path) in written:
        subject = f"mymail digest {daily_path.stem}"
        try:
            send_digest_email(
                account,
                subject=subject,
                body_text=f"Daily, week, and month digests for {account.address}.",
                attachments=[daily_path, week_path, month_path],
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{account.address}: {exc}")
            continue
        _log(f"  emailed {account.address}")
    if errors:
        raise RuntimeError("; ".join(errors))


def cmd_digest(args, conn) -> int:
    written = _write_digests(args, conn)
    if getattr(args, "skip_email", False):
        return 0
    started = _step("email digests")
    try:
        _email_digests(written)
    except Exception as exc:  # noqa: BLE001
        _log(f"  FAILED: {exc}")
        _log(f"  ({time.monotonic() - started:.0f}s)")
        return 1
    _log(f"  ({time.monotonic() - started:.0f}s)")
    return 0


def cmd_daily(args, conn) -> int:
    failures: list[str] = []
    written: list[tuple[Account, tuple[Path, Path, Path]]] = []

    def run(title: str, fn) -> None:
        started = _step(title)
        try:
            fn(args, conn)
        except Exception as exc:  # noqa: BLE001
            failures.append(title)
            _log(f"  FAILED: {exc}")
        _log(f"  ({time.monotonic() - started:.0f}s)")

    def write_digests(step_args, step_conn) -> int:
        written.extend(_write_digests(step_args, step_conn))
        return 0

    def email_digests(_step_args, _step_conn) -> int:
        _email_digests(written)
        return 0

    run("sync", cmd_sync)
    run("reclassify", cmd_reclassify)
    if not args.skip_download:
        run("download", cmd_download)
    run("text", cmd_text)
    run("extract", cmd_extract)
    run("triage", cmd_triage)
    if not args.safe:
        args.apply = True
        run("inbox", cmd_inbox)
        if not args.skip_clean:
            run("clean", cmd_clean)
        if "inbox" in failures:
            run("inbox retry", cmd_inbox)
            if "inbox retry" not in failures:
                failures.remove("inbox")
    run("reconcile", cmd_reconcile)
    run("kb", cmd_kb)
    run("digest", write_digests)
    if not getattr(args, "skip_email", False) and "digest" not in failures:
        run("email digests", email_digests)
    if failures:
        _log(f"\nFinished with failures: {', '.join(failures)} (rerun is safe)")
        return 1
    _log("\nDone.")
    return 0


def cmd_ask(args, conn) -> int:
    conn.close()
    return subprocess.call([sys.executable, str(SCRIPTS / "ask.py"), *args.rest], cwd=REPO_ROOT)


# ------------------------------------------------------------------- main


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="mymail", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--mailbox", default=None, help="Slug, email, or 'all' (default: all)")
        p.set_defaults(fn=fn)
        return p

    def sync_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--full", action="store_true", help="Rescan whole years instead of catching up")
        p.add_argument("--since-year", type=int, default=None)
        p.add_argument("--until-year", type=int, default=None)
        p.add_argument("--limit", type=int, default=0, help="Catalog at most N new messages")

    def download_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--include-accounts", action="store_true")
        p.add_argument("--include-other", action="store_true")
        p.add_argument("--download-limit", type=int, default=0, help="Download at most N bodies")
        p.add_argument("--max-mb", type=float, default=0, help="Skip messages larger than this (MB)")

    def add_skip_email(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--skip-email",
            action="store_true",
            help="Write digests but do not send them (local debugging)",
        )

    add("migrate", cmd_migrate, "Import legacy index + downloaded files")
    sync_flags(add("sync", cmd_sync, "Catalog new mail (incremental)"))
    p = add("download", cmd_download, "Download bodies for signal topics")
    download_flags(p)
    p.add_argument("--dry-run", action="store_true")
    add("text", cmd_text, "Extract plain text").add_argument("--force", action="store_true")
    add("extract", cmd_extract, "Extract facts + full-text index").add_argument("--force", action="store_true")
    add("reclassify", cmd_reclassify, "Re-run the topic classifier over the catalog")
    add("reconcile", cmd_reconcile, "Mark messages no longer in Gmail as gone")
    add("triage", cmd_triage, "Recompute priorities")
    add("inbox", cmd_inbox, "Label/archive Inbox per triage").add_argument("--apply", action="store_true")
    p = add("clean", cmd_clean, "Unsubscribe, delete noise, wipe categories/spam")
    for flag in ("--skip-unsubscribe", "--skip-delete", "--skip-wipe"):
        p.add_argument(flag, action="store_true")
    add("kb", cmd_kb, "Regenerate knowledge-base markdown")
    p = add("digest", cmd_digest, "Write daily, week, and month digests and email them")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Daily digest window only (week and month ignore this)",
    )
    add_skip_email(p)

    p = add("daily", cmd_daily, "Run everything (incremental)")
    sync_flags(p)
    download_flags(p)
    p.add_argument("--safe", action="store_true", help="Read-only on Gmail: no inbox changes, no clean")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--skip-clean", action="store_true")
    add_skip_email(p)
    for flag in ("--skip-unsubscribe", "--skip-delete", "--skip-wipe"):
        p.add_argument(flag, action="store_true")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Daily digest window only (default: since last digest; week and month ignore this)",
    )

    p = sub.add_parser("ask", help="Read-only SQL / presets (forwards to ask.py)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_ask)

    args = parser.parse_args()
    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot open database: {exc}", file=sys.stderr)
        return 1
    try:
        return int(args.fn(args, conn) or 0)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
