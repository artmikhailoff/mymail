"""Digests under data/digests/<mailbox>/.

Daily ``YYYY-MM-DD.md`` covers messages since the previous digest (max 7 days
back), grouped by priority so the important ones are on top.

``week.md`` is a rolling 7×24h window: ``now - timedelta(days=7)`` through
``now`` (inclusive) for messages and cleanup logs.

``month.md`` is calendar month-to-date: local midnight on the 1st through
``now``.

Only the daily builder advances the ``digest_last:<slug>`` watermark.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from scripts.lib.digest_logs import (
    delete_removals_in_window,
    load_jsonl,
    summarize_wipe_log,
    top_senders_from_delete_log,
)
from scripts.lib.paths import DIGESTS_DIR, delete_log_path, wipe_log_path

MAX_LOOKBACK_DAYS = 7
FYI_PER_TOPIC = 15


def _meta_key(slug: str) -> str:
    return f"digest_last:{slug}"


def _cleanup_section(slug: str, since: datetime, now: datetime) -> list[str]:
    """Cleanup stats for the same window as the digest message query."""
    removals = delete_removals_in_window(load_jsonl(delete_log_path(slug)), since, now)
    senders = top_senders_from_delete_log(removals)
    wipes = summarize_wipe_log(load_jsonl(wipe_log_path(slug)), since, now)

    parts = [
        "## Cleanup",
        "",
        f"- removals: {len(removals)}",
        "",
        "### Top senders removed",
        "",
    ]
    if senders:
        parts += [f"- {sender} — {count}" for sender, count in senders]
    else:
        parts.append("_nothing_")

    parts += ["", "### Wiped", ""]
    wipe_lines = [_format_wipe(row) for row in wipes if int(row.get("count") or 0) > 0]
    parts += wipe_lines or ["_nothing_"]
    parts.append("")
    return parts


def _format_wipe(row: dict) -> str:
    target = str(row.get("target") or "?").strip() or "?"
    action = str(row.get("action") or "").strip()
    count = int(row.get("count") or 0)
    if target in ("promotions", "updates") and action == "trash":
        return f"- {target} trashed {count}"
    if target == "spam" and action == "permanent_delete":
        return f"- spam deleted {count}"
    if target == "trash" and action == "empty":
        return f"- trash emptied {count}"
    label = f"{target} {action}".strip()
    return f"- {label}: {count}"


def build_digest(conn: sqlite3.Connection, slug: str, address: str, *, days: int | None = None) -> Path:
    now = datetime.now()
    if days is not None:
        since = now - timedelta(days=days)
    else:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (_meta_key(slug),)).fetchone()
        floor = now - timedelta(days=MAX_LOOKBACK_DAYS)
        since = max(datetime.fromisoformat(row[0]), floor) if row else now - timedelta(days=1)
    since_s = since.strftime("%Y-%m-%dT%H:%M:%S")

    rows = conn.execute(
        "SELECT * FROM messages WHERE mailbox = ? AND date >= ? ORDER BY date DESC",
        (slug, since_s),
    ).fetchall()
    by_priority: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_priority.setdefault(row["priority"] or "archive", []).append(row)

    def line(row: sqlite3.Row) -> str:
        who = row["from_name"] or row["from_email"] or "?"
        return f"- {row['date'][5:16].replace('T', ' ')} — **{who}** — {row['subject'] or '(no subject)'}"

    parts = [
        f"# Mail digest {now:%Y-%m-%d}",
        "",
        f"{address} — since {since:%Y-%m-%d %H:%M}: {len(rows)} message(s).",
        "",
        f"## Needs your attention ({len(by_priority.get('action', []))})",
        "",
    ]
    parts += [line(r) + f"  _({r['priority_reason']})_" for r in by_priority.get("action", [])] or ["_nothing_"]

    fyi = by_priority.get("fyi", [])
    parts += ["", f"## FYI ({len(fyi)})", ""]
    by_topic: dict[str, list[sqlite3.Row]] = {}
    for row in fyi:
        by_topic.setdefault(row["topic"], []).append(row)
    for topic, items in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        parts.append(f"### {topic} ({len(items)})")
        parts += [line(r) for r in items[:FYI_PER_TOPIC]]
        if len(items) > FYI_PER_TOPIC:
            parts.append(f"- … {len(items) - FYI_PER_TOPIC} more")
        parts.append("")
    if not fyi:
        parts.append("_nothing_")

    tx = conn.execute(
        "SELECT date, vendor, kind, amount, project FROM transactions t "
        "WHERE mailbox = ? AND kind IN ('purchase','refund') AND gmail_msgid IN "
        "(SELECT gmail_msgid FROM messages WHERE mailbox = ? AND date >= ?) ORDER BY date",
        (slug, slug, since_s),
    ).fetchall()
    if tx:
        parts += ["", "## New purchases / refunds", ""]
        parts += [
            f"- {r['date']} {r['vendor']} {r['kind']} ${r['amount']:,.2f}"
            + (f" (project {r['project']})" if r["project"] else "")
            for r in tx
        ]

    archived = by_priority.get("archive", [])
    junk = by_priority.get("junk", [])
    parts += [
        "",
        "## Handled automatically",
        "",
        f"- archived (kept, out of Inbox): {len(archived)}",
        f"- junk (unsubscribed/deleted by clean): {len(junk)}",
        "",
    ]
    parts += _cleanup_section(slug, since, now)

    out = DIGESTS_DIR / slug / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_meta_key(slug), now.isoformat(timespec="seconds")),
    )
    conn.commit()
    return out


def build_week_digest(conn: sqlite3.Connection, slug: str, address: str) -> Path:
    """Write ``week.md`` for the rolling last 7 days.

    Window is ``now - timedelta(days=7)`` through ``now`` (inclusive) for
    messages and cleanup logs. Does not advance ``digest_last``.
    """
    now = datetime.now()
    since = now - timedelta(days=7)
    return _write_range_digest(
        conn, slug, address, since=since, now=now, filename="week.md", title="Week digest"
    )


def build_month_digest(conn: sqlite3.Connection, slug: str, address: str) -> Path:
    """Write ``month.md`` for the calendar month to date.

    Window starts at local midnight on the 1st (``datetime(now.year, now.month, 1)``)
    and runs through ``now``. Does not advance ``digest_last``.
    """
    now = datetime.now()
    since = datetime(now.year, now.month, 1)
    return _write_range_digest(
        conn, slug, address, since=since, now=now, filename="month.md", title="Month digest"
    )


def _write_range_digest(
    conn: sqlite3.Connection,
    slug: str,
    address: str,
    *,
    since: datetime,
    now: datetime,
    filename: str,
    title: str,
) -> Path:
    """Same grouping as the daily digest, over an explicit window.

    Does not read or write ``digest_last``. ``--days`` is not applied here.
    """
    since_s = since.strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM messages WHERE mailbox = ? AND date >= ? ORDER BY date DESC",
        (slug, since_s),
    ).fetchall()
    by_priority: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_priority.setdefault(row["priority"] or "archive", []).append(row)

    def line(row: sqlite3.Row) -> str:
        who = row["from_name"] or row["from_email"] or "?"
        return f"- {row['date'][5:16].replace('T', ' ')} — **{who}** — {row['subject'] or '(no subject)'}"

    parts = [
        f"# {title} {since:%Y-%m-%d} to {now:%Y-%m-%d} ({len(rows)} messages)",
        "",
        f"{address} — {since:%Y-%m-%d %H:%M} through {now:%Y-%m-%d %H:%M}: {len(rows)} message(s).",
        "",
        f"## Needs your attention ({len(by_priority.get('action', []))})",
        "",
    ]
    parts += [line(r) + f"  _({r['priority_reason']})_" for r in by_priority.get("action", [])] or ["_nothing_"]

    fyi = by_priority.get("fyi", [])
    parts += ["", f"## FYI ({len(fyi)})", ""]
    by_topic: dict[str, list[sqlite3.Row]] = {}
    for row in fyi:
        by_topic.setdefault(row["topic"], []).append(row)
    for topic, items in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        parts.append(f"### {topic} ({len(items)})")
        parts += [line(r) for r in items[:FYI_PER_TOPIC]]
        if len(items) > FYI_PER_TOPIC:
            parts.append(f"- … {len(items) - FYI_PER_TOPIC} more")
        parts.append("")
    if not fyi:
        parts.append("_nothing_")

    tx = conn.execute(
        "SELECT date, vendor, kind, amount, project FROM transactions t "
        "WHERE mailbox = ? AND kind IN ('purchase','refund') AND gmail_msgid IN "
        "(SELECT gmail_msgid FROM messages WHERE mailbox = ? AND date >= ?) ORDER BY date",
        (slug, slug, since_s),
    ).fetchall()
    if tx:
        parts += ["", "## New purchases / refunds", ""]
        parts += [
            f"- {r['date']} {r['vendor']} {r['kind']} ${r['amount']:,.2f}"
            + (f" (project {r['project']})" if r["project"] else "")
            for r in tx
        ]

    archived = by_priority.get("archive", [])
    junk = by_priority.get("junk", [])
    parts += [
        "",
        "## Handled automatically",
        "",
        f"- archived (kept, out of Inbox): {len(archived)}",
        f"- junk (unsubscribed/deleted by clean): {len(junk)}",
        "",
    ]
    parts += _cleanup_section(slug, since, now)

    out = DIGESTS_DIR / slug / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
