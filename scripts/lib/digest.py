"""Daily digest: data/digests/<mailbox>/YYYY-MM-DD.md.

Covers messages that arrived since the previous digest (max 7 days back),
grouped by priority so the important ones are on top.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from scripts.lib.paths import DIGESTS_DIR

MAX_LOOKBACK_DAYS = 7
FYI_PER_TOPIC = 15


def _meta_key(slug: str) -> str:
    return f"digest_last:{slug}"


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

    out = DIGESTS_DIR / slug / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_meta_key(slug), now.isoformat(timespec="seconds")),
    )
    conn.commit()
    return out
