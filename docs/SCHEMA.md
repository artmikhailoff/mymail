# mail.db schema

`data/store/mail.db` (SQLite, gitignored). One file for all mailboxes; every
table has a `mailbox` column (slug such as `amik1977_at_gmail_com`).
Query it read-only with `python scripts/ask.py "<SQL>"`.

## Catalog

### `messages` — one row per Gmail message (PK `mailbox, gmail_msgid`)

| column | meaning |
|---|---|
| `gmail_msgid` | Gmail X-GM-MSGID (stable forever) |
| `thread_id` | Gmail thread id |
| `date` | ISO internal date; `year` (`YYYY`), `month` (`YYYY-MM`) |
| `from_name`, `from_email`, `from_domain` | sender |
| `to_addr`, `subject`, `labels` (JSON), `size_bytes` | headers |
| `topic` | `noise, health, construction, finance, travel, school, shopping, accounts, personal, other` |
| `confidence`, `reasons` (JSON) | classifier output |
| `priority` | `action, fyi, archive, junk` (from `config/rules.toml`) |
| `priority_reason` | which rule fired |
| `raw_path`, `text_path` | repo-relative `.eml` / `.txt`; NULL = not downloaded |
| `attachments` | comma-separated attachment names |
| `gone` | 1 = no longer in Gmail (trashed/deleted); row kept for history |

### `message_text` — FTS5 full-text index of downloaded bodies

`mailbox, gmail_msgid, subject, body` (body truncated to 20k chars).
Use `MATCH`: `SELECT gmail_msgid FROM message_text WHERE message_text MATCH 'roof AND leak'`.
Or `python scripts/ask.py search "roof leak" --topic construction`.

### `sync_state` — incremental sync watermark

`mailbox, uidvalidity, last_uid, last_sync_at, last_full_scan_at`. A sync fetches
only All Mail UIDs above `last_uid`; if UIDVALIDITY changes it catches up by date.

## Facts (derived, rebuildable)

### `transactions`

| column | meaning |
|---|---|
| `date`, `year` | purchase date |
| `vendor` | `Home Depot`, `Amazon`, `Ally Bank`, ... |
| `category` | `construction, shopping, digital, banking` |
| `kind` | `purchase`, `refund` (negative), `pickup_receipt` (store receipt for a web order already counted), `bank_debit`, `bank_deposit` |
| `amount` | USD; refunds/debits negative |
| `order_ref` | order number / POS store-register-transaction |
| `project` | Home Depot PO/Job name (e.g. `jackman`, `sovy`) |
| `store`, `payment`, `description` (items text) | |
| `extractor`, `confidence`, `dedupe_key` (unique) | |

**Spend** questions use the view `v_spend` (= `kind IN ('purchase','refund')`).
Bank alerts are cash-flow, not spend, and are excluded from `v_spend`.

Coverage today: Home Depot (store e-receipts, online orders, cancellations,
returns), Amazon order confirmations, Ally debit/deposit alerts. Other vendors
are only searchable via `messages` / `message_text`.

### `obligations` — bills, statements, scheduled/made payments

`date, payee, kind (bill|statement|payment_scheduled|payment_made), amount, due_date, account, description`.

### `documents` — signal mail with non-image attachments

`date, topic, sender, title, attachments, raw_path, text_path`.

### `contacts` — per-sender aggregate

`email, name, domain, main_topic, message_count, signal_count, first_date, last_date`.

### `extraction_log`

`(mailbox, gmail_msgid, extractor, version)` processed marker. Bump an
extractor's `version` in `scripts/lib/extract.py` to re-run it.

## Views

- `v_signal` — `messages` minus noise and gone.
- `v_spend` — purchases and refunds.

## Example questions

```sql
-- construction spend per year
SELECT year, ROUND(SUM(amount),2) FROM v_spend WHERE category='construction' GROUP BY year;
-- spend per Home Depot job
SELECT project, ROUND(SUM(amount),2) FROM v_spend WHERE vendor='Home Depot' GROUP BY project ORDER BY 2 DESC;
-- what needs attention
SELECT date, from_name, subject FROM messages WHERE priority='action' ORDER BY date DESC;
-- who emails me most (non-noise)
SELECT email, main_topic, signal_count FROM contacts ORDER BY signal_count DESC LIMIT 20;
-- latest statements/bills by payee
SELECT payee, kind, MAX(date), amount FROM obligations GROUP BY payee, kind;
```
