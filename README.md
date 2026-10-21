# mymail

Personal Gmail census and archive: classify mail into topics, download signal
buckets for agent Q&A, unsubscribe from ads, and trash marketing noise.

## Layout

```
mymail/
  credentials/                 # Optional extra *.env mailboxes (gitignored)
  data/
    mailboxes/<slug>/
      mailbox.json
      index.jsonl              # Header census + topic
      stats.json
      unsubscribe_log.jsonl
      delete_log.jsonl
    raw/<slug>/<topic>/<year>/ # Full .eml + .meta.json (signal topics only)
    processed/<slug>/<topic>/<year>/  # Plain text for agents
    bundles/<slug>/<topic>.md  # Optional @-mention packs
  scripts/
    scan_mailbox.py
    download_bodies.py
    extract_text.py
    build_bundles.py
    report.py / reclassify.py
    unsubscribe.py
    delete_noise.py
```

Year is the finest folder grain. **Noise is never downloaded** into `raw/`.

## Setup

```powershell
cd c:\Users\ffldr\Desktop\code\mymail
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD
```

More accounts: add `credentials/other.env` with the same keys. Select with
`--mailbox all`, `--mailbox amik1977_at_gmail_com`, or an email address.

## Workflow

```powershell
python scripts/scan_mailbox.py --check
python scripts/scan_mailbox.py --since-year 2024
python scripts/report.py

# Download bodies for health/construction/finance/… (not noise)
python scripts/download_bodies.py
python scripts/extract_text.py
python scripts/build_bundles.py

# Ads: unsubscribe headers, then move remaining noise to Trash
python scripts/unsubscribe.py --apply
python scripts/delete_noise.py --apply
```

Ask an agent with `@data/bundles/<slug>/health.md` or
`@data/processed/<slug>/construction/2025/`.

## Topics

| Topic | Meaning | Downloaded? |
|-------|---------|-------------|
| `noise` | Promotions, social, ads | No (unsubscribe + trash) |
| `health` | Labs, clinics, insurance | Yes |
| `construction` | Home improvement / building | Yes |
| `finance` | Banks, cards, taxes | Yes |
| `travel` | Flights, hotels | Yes |
| `school` | ParentSquare, schools | Yes |
| `shopping` | Orders / shipping | Yes |
| `personal` | Human correspondence | Yes |
| `accounts` | OTP / SaaS | Opt-in (`--include-accounts`) |
| `other` | Unclassified | Opt-in (`--include-other`) |

## Delete marketing mail

`delete_noise.py` moves **topic=noise** messages to Gmail Trash (recoverable ~30 days).
Dry-run by default:

```powershell
python scripts/delete_noise.py --limit 50
python scripts/delete_noise.py --apply
python scripts/delete_noise.py --apply --sender shein.com
```

## Privacy

All of `data/` census/archives and `.env` / `credentials/*.env` are **gitignored**.
