"""SQLite knowledge store: data/store/mail.db.

One file for every mailbox. `messages` is the catalog (one row per Gmail
message, keyed by mailbox + X-GM-MSGID); fact tables (`transactions`,
`obligations`, `documents`, `contacts`) are derived from it and can be
rebuilt at any time. `sync_state` makes `mymail sync` incremental.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from scripts.lib.paths import DB_PATH
from scripts.lib.stats import sender_key

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS mailboxes (
    slug        TEXT PRIMARY KEY,
    address     TEXT NOT NULL,
    added_at    TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    mailbox           TEXT PRIMARY KEY REFERENCES mailboxes(slug),
    uidvalidity       INTEGER,
    last_uid          INTEGER,
    last_sync_at      TEXT,
    last_full_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    mailbox         TEXT NOT NULL,
    gmail_msgid     TEXT NOT NULL,
    thread_id       TEXT,
    date            TEXT,            -- ISO internal date (local time from Gmail)
    year            TEXT,
    month           TEXT,            -- YYYY-MM
    from_name       TEXT,
    from_email      TEXT,
    from_domain     TEXT,
    to_addr         TEXT,
    subject         TEXT,
    labels          TEXT,            -- JSON array at catalog time
    size_bytes      INTEGER,
    topic           TEXT,
    confidence      TEXT,
    reasons         TEXT,            -- JSON array
    priority        TEXT,            -- action | fyi | archive | junk
    priority_reason TEXT,
    attachments     TEXT,            -- comma-separated names (after download)
    raw_path        TEXT,            -- repo-relative .eml (NULL = not downloaded)
    text_path       TEXT,            -- repo-relative .txt
    gone            INTEGER NOT NULL DEFAULT 0,  -- 1 = no longer in Gmail
    first_seen_at   TEXT,
    PRIMARY KEY (mailbox, gmail_msgid)
);
CREATE INDEX IF NOT EXISTS ix_messages_topic_year ON messages(topic, year);
CREATE INDEX IF NOT EXISTS ix_messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS ix_messages_domain ON messages(from_domain);
CREATE INDEX IF NOT EXISTS ix_messages_email ON messages(from_email);
CREATE INDEX IF NOT EXISTS ix_messages_priority ON messages(priority, date);
CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(thread_id);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    mailbox      TEXT NOT NULL,
    gmail_msgid  TEXT NOT NULL,
    date         TEXT,               -- YYYY-MM-DD of the purchase
    year         TEXT,
    vendor       TEXT,
    category     TEXT,               -- construction | shopping | travel | ...
    kind         TEXT,               -- purchase | refund | cancellation
    amount       REAL,               -- positive purchase, negative refund
    currency     TEXT DEFAULT 'USD',
    order_ref    TEXT,
    project      TEXT,               -- e.g. Home Depot PO/Job name
    store        TEXT,
    payment      TEXT,
    description  TEXT,
    extractor    TEXT,
    confidence   TEXT,
    dedupe_key   TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_tx_cat_year ON transactions(category, year);
CREATE INDEX IF NOT EXISTS ix_tx_vendor ON transactions(vendor);

CREATE TABLE IF NOT EXISTS obligations (
    id           INTEGER PRIMARY KEY,
    mailbox      TEXT NOT NULL,
    gmail_msgid  TEXT NOT NULL,
    date         TEXT,               -- when the notice arrived
    payee        TEXT,
    kind         TEXT,               -- bill | payment_scheduled | payment_received | statement
    amount       REAL,
    due_date     TEXT,
    account      TEXT,
    description  TEXT,
    extractor    TEXT,
    dedupe_key   TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_obl_payee ON obligations(payee, date);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    mailbox      TEXT NOT NULL,
    gmail_msgid  TEXT NOT NULL,
    date         TEXT,
    topic        TEXT,
    sender       TEXT,
    title        TEXT,
    attachments  TEXT,
    raw_path     TEXT,
    text_path    TEXT,
    dedupe_key   TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_docs_topic ON documents(topic, date);

CREATE TABLE IF NOT EXISTS contacts (
    mailbox        TEXT NOT NULL,
    email          TEXT NOT NULL,
    name           TEXT,
    domain         TEXT,
    main_topic     TEXT,
    message_count  INTEGER,
    signal_count   INTEGER,
    first_date     TEXT,
    last_date      TEXT,
    PRIMARY KEY (mailbox, email)
);

CREATE TABLE IF NOT EXISTS extraction_log (
    mailbox      TEXT NOT NULL,
    gmail_msgid  TEXT NOT NULL,
    extractor    TEXT NOT NULL,
    version      INTEGER NOT NULL,
    result       TEXT,               -- ok | none | error
    ran_at       TEXT,
    PRIMARY KEY (mailbox, gmail_msgid, extractor)
);

CREATE VIRTUAL TABLE IF NOT EXISTS message_text USING fts5(
    mailbox UNINDEXED,
    gmail_msgid UNINDEXED,
    subject,
    body,
    tokenize = 'porter unicode61'
);

CREATE VIEW IF NOT EXISTS v_signal AS
    SELECT * FROM messages WHERE topic <> 'noise' AND gone = 0;

CREATE VIEW IF NOT EXISTS v_spend AS
    SELECT * FROM transactions WHERE kind IN ('purchase', 'refund');
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    db_path = path or DB_PATH
    if readonly:
        if not db_path.is_file():
            raise RuntimeError(
                f"{db_path} does not exist yet. Run: python scripts/mymail.py migrate"
            )
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def ensure_mailbox(conn: sqlite3.Connection, slug: str, address: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO mailboxes(slug, address, added_at) VALUES (?, ?, ?)",
        (slug, address, now_iso()),
    )


def _split_from(from_addr: str) -> tuple[str, str, str]:
    text = (from_addr or "").strip()
    email = sender_key(text)
    name = text[: text.rfind("<")].strip().strip('"') if "<" in text else ""
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return name, email, domain


def message_row(slug: str, record: dict) -> dict:
    date = str(record.get("internal_date") or "")
    name, email, domain = _split_from(str(record.get("from") or ""))
    labels = record.get("labels") or []
    reasons = record.get("reasons") or []
    return {
        "mailbox": slug,
        "gmail_msgid": str(record["gmail_msgid"]),
        "thread_id": str(record.get("thread_id") or "") or None,
        "date": date or None,
        "year": date[:4] if date[:4].isdigit() else "unknown",
        "month": date[:7] if len(date) >= 7 else None,
        "from_name": name or None,
        "from_email": email or None,
        "from_domain": domain or None,
        "to_addr": record.get("to") or None,
        "subject": record.get("subject") or "",
        "labels": json.dumps(list(labels), ensure_ascii=False),
        "size_bytes": record.get("size_bytes"),
        "topic": record.get("topic") or "other",
        "confidence": record.get("confidence"),
        "reasons": json.dumps(list(reasons), ensure_ascii=False),
        "first_seen_at": now_iso(),
    }


_MESSAGE_COLUMNS = (
    "mailbox", "gmail_msgid", "thread_id", "date", "year", "month", "from_name",
    "from_email", "from_domain", "to_addr", "subject", "labels", "size_bytes",
    "topic", "confidence", "reasons", "first_seen_at",
)
_UPSERT_SQL = (
    f"INSERT INTO messages ({', '.join(_MESSAGE_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _MESSAGE_COLUMNS)}) "
    "ON CONFLICT(mailbox, gmail_msgid) DO UPDATE SET "
    + ", ".join(
        f"{c}=excluded.{c}"
        for c in _MESSAGE_COLUMNS
        if c not in ("mailbox", "gmail_msgid", "first_seen_at")
    )
)


def upsert_messages(conn: sqlite3.Connection, slug: str, records: Iterable[dict]) -> int:
    rows = [message_row(slug, r) for r in records if str(r.get("gmail_msgid") or "").isdigit()]
    if rows:
        conn.executemany(_UPSERT_SQL, rows)
    return len(rows)


def known_msgids(conn: sqlite3.Connection, slug: str) -> set[str]:
    cur = conn.execute("SELECT gmail_msgid FROM messages WHERE mailbox = ?", (slug,))
    return {row[0] for row in cur}


def get_sync_state(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sync_state WHERE mailbox = ?", (slug,)).fetchone()


def set_sync_state(
    conn: sqlite3.Connection,
    slug: str,
    *,
    uidvalidity: int | None,
    last_uid: int | None,
    full_scan: bool = False,
) -> None:
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO sync_state(mailbox, uidvalidity, last_uid, last_sync_at, last_full_scan_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(mailbox) DO UPDATE SET
            uidvalidity = excluded.uidvalidity,
            last_uid = excluded.last_uid,
            last_sync_at = excluded.last_sync_at,
            last_full_scan_at = COALESCE(excluded.last_full_scan_at, sync_state.last_full_scan_at)
        """,
        (slug, uidvalidity, last_uid, stamp, stamp if full_scan else None),
    )


def latest_message_date(conn: sqlite3.Connection, slug: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM messages WHERE mailbox = ? AND date IS NOT NULL", (slug,)
    ).fetchone()
    return row[0] if row else None


def junk_msgids(slug: str) -> set[str] | None:
    """msgids triaged as junk, or None when the DB does not exist yet."""
    if not DB_PATH.is_file():
        return None
    conn = connect(readonly=True)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT gmail_msgid FROM messages WHERE mailbox = ? AND priority = 'junk'", (slug,)
            )
        }
    finally:
        conn.close()


def record_from_row(row: sqlite3.Row) -> dict:
    """Shape a messages row like an index.jsonl record (for store helpers)."""
    return {
        "gmail_msgid": row["gmail_msgid"],
        "thread_id": row["thread_id"],
        "internal_date": row["date"],
        "from": (f"{row['from_name']} <{row['from_email']}>" if row["from_name"] else row["from_email"]) or "",
        "to": row["to_addr"] or "",
        "subject": row["subject"] or "",
        "labels": json.loads(row["labels"] or "[]"),
        "size_bytes": row["size_bytes"],
        "topic": row["topic"],
        "confidence": row["confidence"],
        "reasons": json.loads(row["reasons"] or "[]"),
    }


def iter_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Iterator[sqlite3.Row]:
    yield from conn.execute(sql, params)
