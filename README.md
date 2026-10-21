# mymail

Personal Gmail census: scan the mailbox read-only over IMAP, classify messages
into topics (noise, health, construction, finance, …), and report counts.

## Layout

```
mymail/
  data/
    index.jsonl   # One header record per scanned message (gitignored)
    stats.json    # Aggregated counts (gitignored)
  scripts/
    scan_mailbox.py   # IMAP scan → classify → write index + stats
    report.py         # Print stats from data/stats.json (or rebuild from index)
    reclassify.py     # Re-run topic rules on the index
    unsubscribe.py    # List-Unsubscribe for noise/ad senders
    lib/
      gmail.py        # IMAP session + credentials
      classify.py     # Topic rules
      unsubscribe.py  # One-click / mailto / HTTP unsubscribe
      stats.py        # Aggregation
      paths.py
```

## Setup

```powershell
cd c:\Users\ffldr\Desktop\code\mymail
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (same app password as myhealth is fine)
```

## Workflow

Check login:

```powershell
python scripts/scan_mailbox.py --check
```

Dry run (search + classify headers, print counts, write nothing):

```powershell
python scripts/scan_mailbox.py --dry-run --since-year 2024
```

Full census for the last five calendar years (default):

```powershell
python scripts/scan_mailbox.py
python scripts/report.py
```

Limit how many headers are classified this run (newest first within each year):

```powershell
python scripts/scan_mailbox.py --limit 500
```

After editing rules in `scripts/lib/classify.py`, refresh topics without re-downloading:

```powershell
python scripts/reclassify.py
```

## Unsubscribe (noise / ads only)

For senders classified as **noise** (promotions, shopping ads, recommendations),
mymail reads `List-Unsubscribe` / one-click headers and unsubscribes when possible.
One attempt per sender. The mailbox itself is not modified (no deletes).

Preview what would happen:

```powershell
python scripts/unsubscribe.py --limit 30
```

Actually unsubscribe:

```powershell
python scripts/unsubscribe.py --apply
python scripts/unsubscribe.py --apply --sender shein.com --sender michaels.com
```

Results append to `data/unsubscribe_log.jsonl` (gitignored). Already-handled
senders are skipped unless you pass `--force`.

## Topics

| Topic | Meaning |
|-------|---------|
| `noise` | Promotions, social, newsletters, ads |
| `health` | Labs, clinics, insurance, portals |
| `construction` | Home improvement, contractors, building materials |
| `finance` | Banks, cards, taxes, payments |
| `travel` | Flights, hotels, tickets |
| `school` | Schools, ParentSquare, district mail |
| `shopping` | Orders, shipping, marketplaces |
| `accounts` | Security codes, SaaS, account alerts |
| `personal` | Likely human / personal correspondence |
| `other` | Everything else |

Classification uses Gmail categories (Promotions/Social), sender domains, and
subject patterns. Headers only — the mailbox is never modified.

## Privacy

`data/*` census outputs and `.env` are **gitignored**. Only scripts and docs
belong on the remote.
