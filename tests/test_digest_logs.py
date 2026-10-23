"""Unit tests for digest log windows.

Fixtures are inline JSONL strings written to a temp directory. They do not
read ``data/mailboxes``. Week/month ``since`` helpers are not extracted, so
those boundaries are not snapshotted here.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.digest_logs import (  # noqa: E402
    delete_removals_in_window,
    load_jsonl,
    parse_log_timestamp,
    summarize_wipe_log,
    top_senders_from_delete_log,
)

SINCE = datetime(2026, 10, 1, 0, 0, 0)
UNTIL = datetime(2026, 10, 7, 23, 59, 59)

DELETE_JSONL = """\
{"at": "2026-09-30T23:59:59", "status": "ok", "from": "Old Shop <old@shop.example>"}
{"at": "2026-10-01T00:00:00", "status": "ok", "from": "Edge <edge@shop.example>"}
{"at": "2026-10-03T08:00:00", "status": "ok", "from": "Daily Deals <A@news.example>"}
{"at": "2026-10-03T09:00:00", "status": "OK", "from": "Daily Deals <a@news.example>"}
{"at": "2026-10-04T10:00:00", "status": "ok", "from": "Daily Deals <a@news.example>"}
{"at": "2026-10-04T11:00:00", "status": "error", "from": "Daily Deals <a@news.example>"}
{"at": "2026-10-05T12:00:00", "status": "ok", "from": "bare-sender@list.example"}
{"at": "2026-10-05T13:00:00", "status": "ok", "from": "List <bare-sender@list.example>"}
{"at": "2026-10-06T14:00:00", "status": "missing", "from": "Skip <skip@x.example>"}
{"at": "2026-10-07T23:59:59", "status": "ok", "from": "Late <late@x.example>"}
{"at": "2026-10-08T00:00:00", "status": "ok", "from": "Next <next@x.example>"}
{"at": "not-a-timestamp", "status": "ok", "from": "Bad <bad@x.example>"}
{"status": "ok", "from": "No Time <notime@x.example>"}
"""

SENDER_JSONL = """\
{"from": "Ada <Ada@News.Example>"}
{"from": "Ada <ada@news.example>"}
{"from": "Bob <bob@news.example>"}
{"from": "Newsletter"}
{"from": "newsletter"}
{"from": ""}
{"from": null}
"""

WIPE_JSONL = """\
{"at": "2026-10-07T23:59:59", "target": "trash", "action": "empty", "count": 3}
{"at": "2026-10-06T00:00:00", "target": "trash", "action": "empty", "count": 2}
{"at": "2026-10-03T00:00:00", "target": "spam", "action": "permanent_delete", "count": 7}
{"at": "2026-10-02T00:00:00", "target": "updates", "action": "trash", "count": 4}
{"at": "2026-10-02T01:00:00", "target": "updates", "action": "trash", "count": "nope"}
{"at": "2026-10-01T00:00:00", "target": "promotions", "action": "trash", "count": 10}
{"at": "2026-10-05T00:00:00", "target": "promotions", "action": "trash", "count": 5}
{"at": "2026-10-04T00:00:00", "target": "custom", "action": "archive", "count": 1}
{"at": "2026-09-30T23:59:59", "target": "promotions", "action": "trash", "count": 100}
{"at": "2026-10-08T00:00:00", "target": "updates", "action": "trash", "count": 50}
{"at": "bogus", "target": "spam", "action": "permanent_delete", "count": 50}
"""

MALFORMED_JSONL = """

{not json}
{"at": "2026-10-02T00:00:00", "status": "ok", "from": "Kept <kept@ex.com>"}
[1, 2]
"string"
{"ok": true}

"""


def _load(text: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.jsonl"
        path.write_text(text, encoding="utf-8")
        return load_jsonl(path)


class DigestLogTests(unittest.TestCase):
    def test_parse_log_timestamp_naive_and_utc(self) -> None:
        naive = datetime(2026, 10, 3, 15, 30, 0)
        self.assertEqual(parse_log_timestamp({"at": "2026-10-03T15:30:00"}), naive)
        local = (
            datetime(2026, 10, 3, 15, 30, tzinfo=timezone.utc)
            .astimezone()
            .replace(tzinfo=None)
        )
        self.assertEqual(parse_log_timestamp({"at": "2026-10-03T15:30:00Z"}), local)
        self.assertEqual(
            parse_log_timestamp({"at": "2026-10-03T15:30:00+00:00"}),
            local,
        )
        with self.assertRaises(ValueError):
            parse_log_timestamp({"at": "nope"})
        with self.assertRaises(ValueError):
            parse_log_timestamp({})

    def test_delete_rows_filtered_by_at_window(self) -> None:
        rows = _load(DELETE_JSONL)
        kept = delete_removals_in_window(rows, SINCE, UNTIL)
        senders = [row["from"] for row in kept]
        self.assertEqual(
            senders,
            [
                "Edge <edge@shop.example>",
                "Daily Deals <A@news.example>",
                "Daily Deals <a@news.example>",
                "Daily Deals <a@news.example>",
                "bare-sender@list.example",
                "List <bare-sender@list.example>",
                "Late <late@x.example>",
            ],
        )
        self.assertEqual(delete_removals_in_window(rows, UNTIL, SINCE), [])

    def test_top_sender_order_and_counts(self) -> None:
        removals = delete_removals_in_window(_load(DELETE_JSONL), SINCE, UNTIL)
        self.assertEqual(
            top_senders_from_delete_log(removals),
            [
                ("a@news.example", 3),
                ("bare-sender@list.example", 2),
                ("edge@shop.example", 1),
                ("late@x.example", 1),
            ],
        )
        self.assertEqual(
            top_senders_from_delete_log(removals, limit=2),
            [
                ("a@news.example", 3),
                ("bare-sender@list.example", 2),
            ],
        )
        self.assertEqual(top_senders_from_delete_log(removals, limit=0), [])

        ranked = top_senders_from_delete_log(_load(SENDER_JSONL))
        self.assertEqual(
            ranked,
            [
                ("ada@news.example", 2),
                ("newsletter", 2),
                ("(unknown)", 2),
                ("bob@news.example", 1),
            ],
        )

    def test_wipe_log_summarizes_targets_in_window(self) -> None:
        rows = _load(WIPE_JSONL)
        self.assertEqual(
            summarize_wipe_log(rows, SINCE, UNTIL),
            [
                {"target": "promotions", "action": "trash", "count": 15},
                {"target": "updates", "action": "trash", "count": 4},
                {"target": "spam", "action": "permanent_delete", "count": 7},
                {"target": "trash", "action": "empty", "count": 5},
                {"target": "custom", "action": "archive", "count": 1},
            ],
        )
        self.assertEqual(summarize_wipe_log(rows, UNTIL, SINCE), [])

    def test_empty_file_and_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(load_jsonl(empty), [])
            self.assertEqual(load_jsonl(Path(tmp) / "missing.jsonl"), [])

        rows = _load(MALFORMED_JSONL)
        self.assertEqual(
            rows,
            [
                {
                    "at": "2026-10-02T00:00:00",
                    "status": "ok",
                    "from": "Kept <kept@ex.com>",
                },
                {"ok": True},
            ],
        )


if __name__ == "__main__":
    unittest.main()
