"""Make the Gmail Inbox reflect triage.

For every message currently in the Inbox that the catalog knows:
  action  -> label mymail/action (stays in Inbox)
  fyi     -> label mymail/fyi    (stays in Inbox)
  archive -> removed from Inbox (still in All Mail and in the DB)
  junk    -> removed from Inbox now; clean unsubscribes + trashes it from All Mail
Unknown messages (arrived after the last sync) are not touched.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from scripts.lib.accounts import Account
from scripts.lib.gmail import GmailSession, chunks, gmail_msgid, uid_map
from scripts.lib.paths import mailbox_dir

LABELS = {"action": "mymail/action", "fyi": "mymail/fyi"}


def tidy_inbox(
    conn: sqlite3.Connection,
    account: Account,
    *,
    apply: bool,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    slug = account.slug
    priorities = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT gmail_msgid, priority FROM messages WHERE mailbox = ?", (slug,)
        )
    }
    counts = {"inbox": 0, "unknown": 0, "action": 0, "fyi": 0, "archive": 0, "junk": 0}
    plan: dict[str, list[int]] = {"action": [], "fyi": [], "archive": [], "junk": []}
    archived = 0
    failed = 0

    with GmailSession(account.address, account.password, readonly=not apply) as gmail:
        uids = gmail.search_raw("in:inbox")
        counts["inbox"] = len(uids)
        for chunk in chunks(uids, 200):
            for uid, item in uid_map(gmail.fetch(chunk, ["X-GM-MSGID"])).items():
                priority = priorities.get(gmail_msgid(item))
                if priority is None:
                    counts["unknown"] += 1
                    continue
                counts[priority] = counts.get(priority, 0) + 1
                if priority in plan:
                    plan[priority].append(uid)

        log(
            f"  Inbox {counts['inbox']}: action={counts['action']} fyi={counts['fyi']} "
            f"archive={counts['archive']} junk={counts['junk']} not-yet-synced={counts['unknown']}"
        )
        if not apply:
            log("  dry run: nothing changed (use --apply)")
            return counts

        for batch in chunks(plan["archive"] + plan["junk"], 200):
            try:
                archived += gmail.archive(batch)
            except Exception as exc:  # noqa: BLE001 — one batch must not abandon the rest
                failed += len(batch)
                log(f"  archive batch failed ({len(batch)} msgs): {exc}")
        for priority, label in LABELS.items():
            if plan[priority]:
                gmail.create_folder(label)
            for batch in chunks(plan[priority], 200):
                try:
                    gmail.add_labels(batch, [label])
                except Exception as exc:  # noqa: BLE001 — one batch must not abandon the rest
                    failed += len(batch)
                    log(f"  label batch failed ({len(batch)} msgs): {exc}")
        log(f"  labeled action={len(plan['action'])} fyi={len(plan['fyi'])}, archived {archived}")

    log_path = mailbox_dir(slug) / "inbox_log.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    **counts,
                    "archived": archived,
                    "failed": failed,
                }
            )
            + "\n"
        )
    if failed > 0:
        raise RuntimeError(f"inbox tidy: {failed} message(s) not updated")
    return counts
