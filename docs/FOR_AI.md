# Using mymail with Cursor

Mailbox contents are **personal data**: summarize, never commit `data/`.

## Where answers live (cheapest first)

1. `data/kb/<slug>/*.md` — overview, spending, bills, health, contacts, action.
2. `data/store/mail.db` via `python scripts/ask.py` — see `docs/SCHEMA.md`.
   - `ask.py spend --category construction --year 2022 [--items]`
   - `ask.py search "<fts query>" [--topic T] [--year Y]`
   - `ask.py msg <gmail_msgid>`
3. `text_path` files for specific messages only.

## Refresh

`python scripts/mymail.py daily` (incremental). `--safe` = no Gmail changes.

## Deletion policy

- Only `priority = junk` (topic noise, or `[block]` senders) is unsubscribed
  and deleted. `[keep]` senders are never junk.
- `wipe_folders` empties Promotions/Updates but skips every catalogued
  message that is not junk; Spam is deleted outright.
- `archive` priority only removes mail from the Inbox; it stays in Gmail.
