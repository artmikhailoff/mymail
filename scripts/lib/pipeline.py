"""Catalog pipeline: migrate legacy files, incremental sync, download, extract text.

Everything here is idempotent. State lives in data/store/mail.db:
  * sync_state.last_uid    -> next sync only fetches All Mail UIDs above it
  * messages.raw_path      -> body already downloaded (never fetched again)
  * messages.text_path     -> plain text already extracted
  * messages.gone          -> message vanished from Gmail; stop retrying
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from scripts.lib import db
from scripts.lib.accounts import Account
from scripts.lib.classify import classify_record
from scripts.lib.gmail import (
    META_FIELDS,
    SCOPE,
    GmailSession,
    chunks,
    envelope_record,
    search_year,
    uid_map,
    years_to_scan,
)
from scripts.lib.paths import (
    DOWNLOAD_TOPICS,
    PROCESSED_DIR,
    RAW_DIR,
    REPO_ROOT,
    delete_log_path,
    index_path,
    processed_text_path,
    raw_message_path,
    rel_path,
)
from scripts.lib.stats import load_index
from scripts.lib.store import save_raw_message, write_processed_text

Log = Callable[[str], None]

CATCH_UP_OVERLAP_DAYS = 2
COMMIT_EVERY = 200


# ---------------------------------------------------------------- migrate


def _attachments_from_text(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(10):
                line = handle.readline()
                if not line:
                    break
                if line.startswith("Attachments: "):
                    return line[len("Attachments: "):].strip() or None
    except OSError:
        return None
    return None


def migrate_mailbox(conn: sqlite3.Connection, account: Account, log: Log = print) -> dict:
    """Import index.jsonl and register already-downloaded files. Safe to rerun."""
    slug = account.slug
    db.ensure_mailbox(conn, slug, account.address)

    records = load_index(index_path(slug))
    imported = db.upsert_messages(conn, slug, records)
    conn.commit()
    log(f"  catalog: {imported} index rows -> {len(db.known_msgids(conn, slug))} unique messages")

    raw_root = RAW_DIR / slug
    registered = 0
    orphans = 0
    for eml in raw_root.rglob("*.eml") if raw_root.is_dir() else []:
        msgid = eml.stem
        exists = conn.execute(
            "SELECT 1 FROM messages WHERE mailbox = ? AND gmail_msgid = ?", (slug, msgid)
        ).fetchone()
        if not exists:
            meta_path = eml.with_suffix(".meta.json")
            if not meta_path.is_file():
                orphans += 1
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            db.upsert_messages(conn, slug, [meta])
        txt = PROCESSED_DIR / eml.relative_to(RAW_DIR).with_suffix(".txt")
        conn.execute(
            "UPDATE messages SET raw_path = ?, text_path = ?, attachments = ?, gone = 0 "
            "WHERE mailbox = ? AND gmail_msgid = ?",
            (
                rel_path(eml),
                rel_path(txt) if txt.is_file() else None,
                _attachments_from_text(txt) if txt.is_file() else None,
                slug,
                msgid,
            ),
        )
        registered += 1
    conn.commit()
    log(f"  downloaded bodies registered: {registered} (orphans without meta: {orphans})")

    trashed = 0
    log_path = delete_log_path(slug)
    if log_path.is_file():
        ids = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") in ("ok", "missing") and row.get("gmail_msgid"):
                ids.append((slug, str(row["gmail_msgid"])))
        conn.executemany(
            "UPDATE messages SET gone = 1 WHERE mailbox = ? AND gmail_msgid = ? AND raw_path IS NULL",
            ids,
        )
        trashed = len(ids)
        conn.commit()
    log(f"  marked gone from delete log: {trashed}")
    return {"messages": imported, "registered": registered, "gone": trashed}


# ------------------------------------------------------------------- sync


def _fetch_new(
    gmail: GmailSession,
    uids: list[int],
    known: set[str],
    *,
    limit: int,
    log: Log,
) -> tuple[list[dict], int, int]:
    """Fetch headers for uids (ascending), classify the unseen ones.

    Returns (records, highest_uid_processed, skipped_known).
    """
    records: list[dict] = []
    skipped = 0
    highest = 0
    total = len(uids)
    for index, chunk in enumerate(chunks(sorted(uids), 100), start=1):
        if limit and len(records) >= limit:
            break
        fetched = uid_map(gmail.fetch(chunk, META_FIELDS))
        if index == 1 or index % 10 == 0:
            log(f"  headers {min(index * 100, total)}/{total}")
        for uid in chunk:
            if limit and len(records) >= limit:
                break
            highest = max(highest, uid)
            item = fetched.get(uid)
            if not item:
                continue
            try:
                record = envelope_record(item)
            except RuntimeError:
                continue
            if "\\Draft" in record.get("labels", []):
                continue
            msgid = record["gmail_msgid"]
            if not msgid or msgid in known:
                skipped += 1
                continue
            result = classify_record(record)
            record["topic"] = result.topic
            record["reasons"] = list(result.reasons)
            record["confidence"] = result.confidence
            records.append(record)
            known.add(msgid)
    return records, highest, skipped


def _append_index(slug: str, records: list[dict]) -> None:
    """Keep index.jsonl in step: the cleanse scripts still read it."""
    if not records:
        return
    path = index_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps({**record, "mailbox": slug}, ensure_ascii=False) + "\n")


def sync_mailbox(
    conn: sqlite3.Connection,
    account: Account,
    *,
    full: bool = False,
    since_year: int | None = None,
    until_year: int | None = None,
    limit: int = 0,
    log: Log = print,
) -> dict:
    slug = account.slug
    db.ensure_mailbox(conn, slug, account.address)
    conn.commit()  # never hold the write lock across the IMAP scan
    known = db.known_msgids(conn, slug)
    state = db.get_sync_state(conn, slug)
    this_year = datetime.now().year

    with GmailSession(account.address, account.password) as gmail:
        validity, uidnext = gmail.uidvalidity, gmail.uidnext
        log(f"  All Mail: {gmail.exists} messages, UIDVALIDITY={validity}, UIDNEXT={uidnext}")

        if full:
            mode = "full"
            uids: set[int] = set()
            for year in years_to_scan(since_year or this_year - 4, until_year or this_year):
                found, _ = search_year(gmail, year)
                log(f"  {year}: {len(found)} in Gmail")
                uids.update(found)
        elif state and state["last_uid"] and state["uidvalidity"] == validity:
            mode = "incremental"
            uids = set(gmail.search_uids_after(int(state["last_uid"])))
            log(f"  incremental: {len(uids)} new UID(s) above {state['last_uid']}")
        else:
            latest = db.latest_message_date(conn, slug)
            if latest:
                mode = "catch-up"
                since = datetime.fromisoformat(latest[:19]) - timedelta(days=CATCH_UP_OVERLAP_DAYS)
                query = f"after:{since:%Y/%m/%d} {SCOPE}"
                reason = "UIDVALIDITY changed" if state else "first incremental run"
                log(f"  catch-up ({reason}) since {since:%Y-%m-%d}")
                uids = set(gmail.search_raw(query))
            else:
                mode = "full"
                uids = set()
                for year in years_to_scan(since_year or this_year - 4, until_year or this_year):
                    found, _ = search_year(gmail, year)
                    log(f"  {year}: {len(found)} in Gmail")
                    uids.update(found)

        records, highest, skipped = _fetch_new(gmail, sorted(uids), known, limit=limit, log=log)

    db.upsert_messages(conn, slug, records)
    _append_index(slug, records)

    truncated = bool(limit) and len(records) >= limit
    if mode == "incremental":
        new_last = max(int(state["last_uid"]), highest)
    elif truncated:
        new_last = None  # finish the catch-up next run
    else:
        new_last = max(highest, (uidnext or 1) - 1)
    if new_last is not None or mode == "incremental":
        db.set_sync_state(
            conn, slug, uidvalidity=validity, last_uid=new_last, full_scan=(mode == "full")
        )
    conn.commit()

    by_topic: dict[str, int] = {}
    for record in records:
        by_topic[record["topic"]] = by_topic.get(record["topic"], 0) + 1
    log(
        f"  {mode}: {len(records)} new, {skipped} already known"
        + (f" | {', '.join(f'{k}={v}' for k, v in sorted(by_topic.items()))}" if by_topic else "")
    )
    return {"mode": mode, "new": len(records), "skipped": skipped, "by_topic": by_topic}


def reconcile_mailbox(conn: sqlite3.Connection, account: Account, log: Log = print) -> dict:
    """Mark messages no longer in All Mail as gone (and revived ones as present)."""
    slug = account.slug
    present: set[str] = set()
    with GmailSession(account.address, account.password) as gmail:
        uids = gmail.search_all_uids()
        for index, chunk in enumerate(chunks(uids, 1000), start=1):
            for item in gmail.fetch(chunk, ["X-GM-MSGID"]).values():
                msgid = item.get(b"X-GM-MSGID", item.get("X-GM-MSGID"))
                if msgid is not None:
                    present.add(str(msgid))
            if index % 10 == 0:
                log(f"  ids {min(index * 1000, len(uids))}/{len(uids)}")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS present_ids (gmail_msgid TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM present_ids")
    conn.executemany("INSERT OR IGNORE INTO present_ids VALUES (?)", [(m,) for m in present])
    gone = conn.execute(
        "UPDATE messages SET gone = 1 WHERE mailbox = ? AND gone = 0 "
        "AND gmail_msgid NOT IN (SELECT gmail_msgid FROM present_ids)",
        (slug,),
    ).rowcount
    back = conn.execute(
        "UPDATE messages SET gone = 0 WHERE mailbox = ? AND gone = 1 "
        "AND gmail_msgid IN (SELECT gmail_msgid FROM present_ids)",
        (slug,),
    ).rowcount
    unknown = len(present - db.known_msgids(conn, slug))
    conn.commit()
    log(f"  All Mail holds {len(present)}; newly gone {gone}, back {back}, not catalogued {unknown}")
    return {"present": len(present), "gone": gone, "back": back, "unknown": unknown}


def reclassify_all(conn: sqlite3.Connection) -> dict[str, int]:
    """Re-run the classifier over every catalogued message (headers only)."""
    changes: dict[str, int] = {}
    updates = []
    for row in conn.execute("SELECT * FROM messages"):
        result = classify_record(db.record_from_row(row))
        if result.topic != row["topic"]:
            key = f"{row['topic']}->{result.topic}"
            changes[key] = changes.get(key, 0) + 1
            updates.append(
                (result.topic, result.confidence, json.dumps(list(result.reasons)),
                 row["mailbox"], row["gmail_msgid"])
            )
    conn.executemany(
        "UPDATE messages SET topic = ?, confidence = ?, reasons = ? WHERE mailbox = ? AND gmail_msgid = ?",
        updates,
    )
    conn.commit()
    return changes


# --------------------------------------------------------------- download


def download_topics(include_accounts: bool = False, include_other: bool = False) -> set[str]:
    wanted = set(DOWNLOAD_TOPICS)
    if include_accounts:
        wanted.add("accounts")
    if include_other:
        wanted.add("other")
    wanted.discard("noise")
    return wanted


def download_bodies(
    conn: sqlite3.Connection,
    account: Account,
    *,
    topics: set[str],
    limit: int = 0,
    max_bytes: int = 0,
    dry_run: bool = False,
    log: Log = print,
) -> dict:
    slug = account.slug
    marks = ",".join("?" for _ in topics)
    size_clause = " AND COALESCE(size_bytes, 0) <= ?" if max_bytes else ""
    rows = conn.execute(
        f"SELECT * FROM messages WHERE mailbox = ? AND topic IN ({marks}) "
        f"AND raw_path IS NULL AND gone = 0{size_clause} ORDER BY date DESC",
        (slug, *sorted(topics), *([max_bytes] if max_bytes else [])),
    ).fetchall()

    # Files already on disk (e.g. from an older run) are registered, not refetched.
    pending = []
    adopted = 0
    for row in rows:
        record = db.record_from_row(row)
        path = raw_message_path(slug, record["topic"], row["year"], row["gmail_msgid"])
        if path.is_file():
            conn.execute(
                "UPDATE messages SET raw_path = ? WHERE mailbox = ? AND gmail_msgid = ?",
                (rel_path(path), slug, row["gmail_msgid"]),
            )
            adopted += 1
        else:
            pending.append(record)
    conn.commit()
    if limit:
        pending = pending[:limit]
    log(f"  {len(pending)} body(ies) to download, {adopted} found on disk")
    if dry_run or not pending:
        for record in pending[:20]:
            log(f"  WOULD {record['topic']} | {record['from']} | {record['subject']}")
        return {"pending": len(pending), "saved": 0, "gone": 0}

    saved = gone = 0
    with GmailSession(account.address, account.password) as gmail:
        for index, record in enumerate(pending, start=1):
            msgid = record["gmail_msgid"]
            try:
                uids = gmail.search_gm_msgid(msgid)
                if not uids:
                    conn.execute(
                        "UPDATE messages SET gone = 1 WHERE mailbox = ? AND gmail_msgid = ?",
                        (slug, msgid),
                    )
                    gone += 1
                    continue
                body = gmail.fetch_body(uids[0])
                out = save_raw_message(slug=slug, record=record, body=body)
                conn.execute(
                    "UPDATE messages SET raw_path = ? WHERE mailbox = ? AND gmail_msgid = ?",
                    (rel_path(out), slug, msgid),
                )
                saved += 1
                if index % 25 == 0 or index == len(pending):
                    log(f"  [{index}/{len(pending)}] saved")
            except Exception as exc:  # noqa: BLE001
                log(f"  skip {msgid}: {exc}")
            if index % COMMIT_EVERY == 0:
                conn.commit()
    conn.commit()
    log(f"  downloaded {saved}, gone from Gmail {gone}")
    return {"pending": len(pending), "saved": saved, "gone": gone}


# ----------------------------------------------------------- text extract


def extract_texts(conn: sqlite3.Connection, slug: str, *, force: bool = False, log: Log = print) -> int:
    where = "raw_path IS NOT NULL" + ("" if force else " AND text_path IS NULL")
    rows = conn.execute(
        f"SELECT * FROM messages WHERE mailbox = ? AND {where}", (slug,)
    ).fetchall()
    done = 0
    for row in rows:
        eml = REPO_ROOT / row["raw_path"]
        if not eml.is_file():
            conn.execute(
                "UPDATE messages SET raw_path = NULL, text_path = NULL "
                "WHERE mailbox = ? AND gmail_msgid = ?",
                (slug, row["gmail_msgid"]),
            )
            continue
        # Text mirrors the raw file's topic/year folder even if the topic changed since.
        topic, year = Path(row["raw_path"]).parts[-3], Path(row["raw_path"]).parts[-2]
        record = {**db.record_from_row(row), "topic": topic}
        out = processed_text_path(slug, topic, year, row["gmail_msgid"])
        if force or not out.is_file():
            try:
                out = write_processed_text(slug=slug, record=record, eml_path=eml)
            except Exception as exc:  # noqa: BLE001
                log(f"  text failed {row['gmail_msgid']}: {exc}")
                continue
        conn.execute(
            "UPDATE messages SET text_path = ?, attachments = ? WHERE mailbox = ? AND gmail_msgid = ?",
            (rel_path(out), _attachments_from_text(out), slug, row["gmail_msgid"]),
        )
        done += 1
    conn.commit()
    log(f"  extracted text for {done} message(s)")
    return done
