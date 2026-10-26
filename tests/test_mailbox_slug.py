"""Mailbox directory slugs from email addresses. No network or credentials."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.paths import mailbox_slug  # noqa: E402


class MailboxSlugTests(unittest.TestCase):
    def test_ffld_rider_data_directory(self) -> None:
        self.assertEqual(
            mailbox_slug("ffld.rider@gmail.com"),
            "ffld_rider_at_gmail_com",
        )

    def test_primary_mailbox_convention(self) -> None:
        self.assertEqual(
            mailbox_slug("amik1977@gmail.com"),
            "amik1977_at_gmail_com",
        )


if __name__ == "__main__":
    unittest.main()
