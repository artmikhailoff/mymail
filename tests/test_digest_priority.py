"""Week digest keeps action mail after the 2-day inbox demotion.

The stored ``priority`` may already be ``archive``. Sectioning uses
``priority_for`` without age cutoffs.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.digest import build_week_digest  # noqa: E402

SLUG = "digest_priority_test"
ADDRESS = "me@example.com"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE messages (
            mailbox TEXT NOT NULL,
            gmail_msgid TEXT NOT NULL,
            date TEXT,
            from_name TEXT,
            from_email TEXT,
            subject TEXT,
            topic TEXT,
            priority TEXT,
            priority_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            mailbox TEXT,
            gmail_msgid TEXT,
            date TEXT,
            vendor TEXT,
            kind TEXT,
            amount REAL,
            project TEXT
        )
        """
    )
    return conn


class DigestPreDemotionPriorityTests(unittest.TestCase):
    def test_week_digest_keeps_action_after_two_day_demotion(self) -> None:
        when = datetime.now() - timedelta(days=5)
        conn = _conn()
        conn.execute(
            """
            INSERT INTO messages (
                mailbox, gmail_msgid, date, from_name, from_email,
                subject, topic, priority, priority_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SLUG,
                "msg-action-5d",
                when.strftime("%Y-%m-%dT%H:%M:%S"),
                "Ada",
                "ada@family.example",
                "Lunch tomorrow",
                "personal",
                "archive",
                "from a person (older than 2d)",
            ),
        )
        conn.commit()

        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.lib.digest.DIGESTS_DIR", Path(tmp)):
                path = build_week_digest(conn, SLUG, ADDRESS)
            text = path.read_text(encoding="utf-8")

        attention, _, _rest = text.partition("## FYI")
        self.assertIn("## Needs your attention (1)", attention)
        self.assertIn("Lunch tomorrow", attention)
        self.assertIn("from a person (older than 2d)", attention)
        self.assertIn("- archived (kept, out of Inbox): 0", text)

        stored = conn.execute(
            "SELECT priority FROM messages WHERE gmail_msgid = ?",
            ("msg-action-5d",),
        ).fetchone()
        self.assertEqual(stored["priority"], "archive")
        conn.close()


if __name__ == "__main__":
    unittest.main()
