"""Unit tests for tidy_inbox (archive junk, batch tolerance, dry-run).

Uses a fake GmailSession patched into scripts.lib.inbox — no live IMAP.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import Account  # noqa: E402
from scripts.lib.inbox import LABELS, tidy_inbox  # noqa: E402

MAILBOX = "test_at_example_com"
ACCOUNT = Account(
    address="test@example.com",
    password="secret",
    slug=MAILBOX,
    source="test",
)


def _conn_with_priorities(rows: list[tuple[str, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE messages (
            mailbox TEXT NOT NULL,
            gmail_msgid TEXT NOT NULL,
            priority TEXT NOT NULL,
            PRIMARY KEY (mailbox, gmail_msgid)
        )
        """
    )
    for msgid, priority in rows:
        conn.execute(
            "INSERT INTO messages (mailbox, gmail_msgid, priority) VALUES (?, ?, ?)",
            (MAILBOX, msgid, priority),
        )
    conn.commit()
    return conn


def _fetch_item(gmail_msgid: str) -> dict:
    return {"X-GM-MSGID": gmail_msgid}


class FakeGmailSession:
    """Minimal GmailSession stand-in for tidy_inbox."""

    def __init__(self, address: str, password: str, *, readonly: bool = True) -> None:
        self.address = address
        self.password = password
        self.readonly = readonly
        self.inbox_uids: list[int] = []
        self.uid_to_msgid: dict[int, str] = {}
        self.archive_fail_on_call: int | None = None
        self.archive_calls: list[list[int]] = []
        self.archived_total = 0
        self.label_calls: list[tuple[list[int], list[str]]] = []
        self.folders_created: list[str] = []

    def __enter__(self) -> FakeGmailSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def search_raw(self, query: str) -> list[int]:
        assert query == "in:inbox"
        return list(self.inbox_uids)

    def fetch(self, uids: list[int], fields: list[str]) -> dict:
        assert fields == ["X-GM-MSGID"]
        return {uid: _fetch_item(self.uid_to_msgid[uid]) for uid in uids}

    def archive(self, uids: list[int]) -> int:
        call_index = len(self.archive_calls)
        self.archive_calls.append(list(uids))
        if self.archive_fail_on_call == call_index:
            raise TimeoutError("simulated batch failure")
        self.archived_total += len(uids)
        return len(uids)

    def create_folder(self, name: str) -> None:
        self.folders_created.append(name)

    def add_labels(self, uids: list[int], labels: list[str]) -> None:
        self.label_calls.append((list(uids), list(labels)))


class TidyInboxTests(unittest.TestCase):
    def test_archives_junk_and_archive_labels_action_fyi(self) -> None:
        priorities = [
            ("1001", "action"),
            ("1002", "fyi"),
            ("1003", "archive"),
            ("1004", "junk"),
        ]
        uid_plan = [(1, "1001"), (2, "1002"), (3, "1003"), (4, "1004")]
        fake = FakeGmailSession(ACCOUNT.address, ACCOUNT.password, readonly=False)
        fake.inbox_uids = [uid for uid, _ in uid_plan]
        fake.uid_to_msgid = dict(uid_plan)
        conn = _conn_with_priorities(priorities)
        logs: list[str] = []

        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.lib.inbox.GmailSession", return_value=fake
        ), patch("scripts.lib.inbox.mailbox_dir", return_value=Path(tmp)):
            counts = tidy_inbox(conn, ACCOUNT, apply=True, log=logs.append)

        self.assertEqual(counts["action"], 1)
        self.assertEqual(counts["junk"], 1)
        self.assertEqual(counts["archive"], 1)
        archived_uids = [uid for batch in fake.archive_calls for uid in batch]
        self.assertEqual(sorted(archived_uids), [3, 4])
        self.assertEqual(fake.archived_total, 2)

        by_label: dict[str, list[int]] = {}
        for uids, labels in fake.label_calls:
            for label in labels:
                by_label.setdefault(label, []).extend(uids)
        self.assertEqual(by_label.get(LABELS["action"]), [1])
        self.assertEqual(by_label.get(LABELS["fyi"]), [2])
        self.assertIn(LABELS["action"], fake.folders_created)
        self.assertIn(LABELS["fyi"], fake.folders_created)

    def test_partial_archive_batch_failure_still_logs_and_raises(self) -> None:
        archive_msgids = [str(2000 + i) for i in range(250)]
        priorities = [(msgid, "archive") for msgid in archive_msgids]
        uid_plan = [(i + 1, msgid) for i, msgid in enumerate(archive_msgids)]

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            fake = FakeGmailSession(ACCOUNT.address, ACCOUNT.password, readonly=False)
            fake.inbox_uids = [uid for uid, _ in uid_plan]
            fake.uid_to_msgid = dict(uid_plan)
            fake.archive_fail_on_call = 0
            conn = _conn_with_priorities(priorities)
            logs: list[str] = []

            with patch("scripts.lib.inbox.GmailSession", return_value=fake), patch(
                "scripts.lib.inbox.mailbox_dir", return_value=log_dir
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    tidy_inbox(conn, ACCOUNT, apply=True, log=logs.append)

            self.assertIn("200 message(s) not updated", str(ctx.exception))
            self.assertEqual(len(fake.archive_calls), 2)
            self.assertEqual(fake.archived_total, 50)

            log_path = log_dir / "inbox_log.jsonl"
            self.assertTrue(log_path.is_file())
            row = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(row["archive"], 250)
            self.assertEqual(row["archived"], 50)
            self.assertEqual(row["failed"], 200)
            self.assertTrue(any("archive batch failed" in line for line in logs))

    def test_dry_run_does_not_mutate_or_log(self) -> None:
        priorities = [
            ("3001", "action"),
            ("3002", "archive"),
            ("3003", "junk"),
        ]
        uid_plan = [(10, "3001"), (11, "3002"), (12, "3003")]

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            fake = FakeGmailSession(ACCOUNT.address, ACCOUNT.password, readonly=True)
            fake.inbox_uids = [uid for uid, _ in uid_plan]
            fake.uid_to_msgid = dict(uid_plan)
            conn = _conn_with_priorities(priorities)
            logs: list[str] = []

            with patch("scripts.lib.inbox.GmailSession", return_value=fake), patch(
                "scripts.lib.inbox.mailbox_dir", return_value=log_dir
            ):
                counts = tidy_inbox(conn, ACCOUNT, apply=False, log=logs.append)

            self.assertEqual(counts["archive"], 1)
            self.assertEqual(counts["junk"], 1)
            self.assertEqual(fake.archive_calls, [])
            self.assertEqual(fake.label_calls, [])
            self.assertFalse((log_dir / "inbox_log.jsonl").exists())
            self.assertTrue(any("dry run" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
