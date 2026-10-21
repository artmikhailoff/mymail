# Using mymail with Cursor

1. Ensure `.env` has `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (IMAP app password).
2. Run `python scripts/scan_mailbox.py --check` to verify login.
3. Run `python scripts/scan_mailbox.py` (optional `--since-year`, `--limit`, `--dry-run`).
4. Read counts with `python scripts/report.py` or open `data/stats.json`.

The agent should treat mailbox contents as **personal data**. Prefer summarizing
topic counts and top senders; do not paste large volumes of subjects/bodies into
commits or public docs.

Classification rules live in `scripts/lib/classify.py` and can be extended with
new domains or subject patterns when a topic is under-counted.

To drop commercial noise: `python scripts/unsubscribe.py` (dry-run), then
`--apply`. Only `topic=noise` senders; uses List-Unsubscribe / one-click when
present. Do not unsubscribe health, finance, school, or personal mail.
