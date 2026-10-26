# mymail

Personal Gmail assistant: keep the Inbox down to mail that actually needs you,
archive everything worth keeping into a local SQLite knowledge base, and kill
marketing noise (unsubscribe + delete).

## Main command

```powershell
python scripts/mymail.py daily
```

Runs, incrementally and safe to rerun:

1. **sync** – catalog new mail only (All Mail UIDs above the last watermark)
2. **download** – fetch bodies for signal topics not downloaded yet
3. **text** – plain text for new bodies
4. **extract** – transactions / bills / documents / contacts + full-text index
5. **triage** – priority `action | fyi | archive | junk` from `config/rules.toml`
6. **inbox** – label `mymail/action`, `mymail/fyi`; archive the rest out of Inbox
7. **clean** – unsubscribe + delete junk, wipe Promotions/Updates (junk and
   uncatalogued only; anything triage keeps is spared) and Spam, empty Trash
8. **reconcile** – mark catalogued messages that are no longer in Gmail as `gone`
9. **kb** – regenerate `data/kb/<mailbox>/*.md`
10. **digest** – `data/digests/<mailbox>/YYYY-MM-DD.md`, `week.md`, and `month.md`,
    then email those three digests to that mailbox

(`reclassify` also runs right after sync so classifier fixes apply retroactively.)
Action items older than 2 days become fyi, fyi older than 2 days leaves the
Inbox — both tunable in `config/rules.toml`.

Variants:

```powershell
python scripts/mymail.py daily --safe          # read-only on Gmail (no inbox/clean)
python scripts/mymail.py daily --skip-clean    # inbox tidy but no unsub/delete
python scripts/mymail.py daily --skip-email    # write digests, do not send them (testing)
python scripts/mymail.py daily --max-mb 5      # skip downloading huge messages
python scripts/mymail.py sync --full --since-year 2008   # rescan whole years
```

Each step is also a subcommand (`sync`, `download`, `text`, `extract`, `triage`,
`inbox [--apply]`, `clean`, `kb`, `digest`, `migrate`). `run_all.py` is an alias
for `daily`. `--skip-email` on `daily` or `digest` writes the files and skips
the send.

## Morning automation

Full pipeline every day at 08:00 (no `--safe`, `--skip-clean`, or `--skip-email`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_morning_task.ps1
```

That registers scheduled task `mymail-morning` (daily 08:00, StartWhenAvailable,
WakeToRun, Interactive — stay logged on). `scripts/morning.ps1` runs
`python -u scripts/mymail.py daily` and logs to `data/logs/morning-YYYY-MM-DD.log`.

After each mailbox’s digests are written (`YYYY-MM-DD.md`, `week.md`, `month.md`),
they are emailed to that mailbox.

## Asking questions

```powershell
python scripts/ask.py spend --category construction --year 2022
python scripts/ask.py spend --by "project" --vendor "Home Depot"
python scripts/ask.py search "warranty" --topic construction
python scripts/ask.py "SELECT date, subject FROM messages WHERE priority='action'"
python scripts/ask.py schema
```

In Cursor, just ask; `.cursor/rules/mymail.mdc` tells the agent to read
`data/kb/` and query the DB before opening raw mail. Schema: `docs/SCHEMA.md`.

## Incremental state

- `sync_state.last_uid` per mailbox: next sync only looks at newer UIDs
  (if Gmail resets UIDVALIDITY, sync catches up by date instead).
- `messages.raw_path` / `text_path`: a body is downloaded and extracted once.
- `extraction_log`: each extractor runs once per message per version.
- `messages.gone`: messages deleted from Gmail stay in the catalog, never refetched.

## Layout

```
config/rules.toml                 # VIP / keep / block senders, action & archive patterns
data/                             # all gitignored (personal data)
  store/mail.db                   # SQLite catalog + facts + FTS
  kb/<slug>/*.md                  # small pre-aggregated summaries
  digests/<slug>/YYYY-MM-DD.md week.md month.md
  logs/                           # morning-YYYY-MM-DD.log
  raw/<slug>/<topic>/<year>/      # .eml + .meta.json (signal topics only)
  processed/<slug>/<topic>/<year>/# .txt
  mailboxes/<slug>/               # index.jsonl + unsubscribe/delete/wipe/inbox logs
scripts/
  mymail.py                       # CLI (main entry)
  ask.py                          # read-only queries
  lib/{db,pipeline,triage,extract,kb,digest,inbox,classify,gmail,...}.py
  unsubscribe.py delete_noise.py wipe_folders.py   # used by `clean`
  scan_mailbox.py download_bodies.py ...           # legacy single steps
```

## Setup

```powershell
cd c:\Users\ffldr\Desktop\code\mymail
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # GMAIL_ADDRESS + GMAIL_APP_PASSWORD (IMAP app password)
```

More accounts: `credentials/<name>.env` with the same keys; select with
`--mailbox all | <slug> | <email>`.

## Topics

| Topic | Meaning | Downloaded? |
|-------|---------|-------------|
| `noise` | Promotions, social, ads | No (unsubscribe + delete) |
| `health`, `construction`, `finance`, `travel`, `school`, `shopping`, `personal` | signal | Yes |
| `accounts` | OTP / SaaS | Opt-in (`--include-accounts`) |
| `other` | Unclassified | Opt-in (`--include-other`) |

Senders in `[keep]` (banks, IRS...) are never treated as junk even if the
classifier says noise.

## Privacy

`data/`, `.env` and `credentials/*.env` are gitignored. Never commit them.
