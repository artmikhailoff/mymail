# Using mymail with Cursor

## Data layout

```
data/mailboxes/<slug>/index.jsonl     # census + topic labels
data/raw/<slug>/<topic>/<year>/       # full .eml (signal topics only)
data/processed/<slug>/<topic>/<year>/ # plain text for reading
data/bundles/<slug>/<topic>.md        # curated packs for @-mentions
```

Year is the finest folder grain. Noise/ads are **not** stored under `raw/`.

## Typical agent workflow

1. Ensure `.env` (and optional `credentials/*.env`) have IMAP app passwords.
2. `python scripts/scan_mailbox.py` then `download_bodies.py` → `extract_text.py` → `build_bundles.py`.
3. Attach `@data/bundles/<slug>/health.md` (or another topic), or point at
   `data/processed/<slug>/<topic>/<year>/`.

Treat mailbox contents as **personal data**. Prefer summarizing; do not commit
bodies or indexes.

## Marketing noise

- Classify → `topic=noise`
- `python scripts/unsubscribe.py --apply` (List-Unsubscribe when present)
- `python scripts/delete_noise.py --apply` (move to Gmail Trash)

Do **not** delete health, finance, school, personal, or real shopping/order mail.
