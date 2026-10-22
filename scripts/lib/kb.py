"""Generate data/kb/<mailbox>/*.md: small, pre-aggregated answers.

These files are what an agent should read first. They are regenerated from
mail.db on every run and kept short (tables of totals, recent items).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from scripts.lib.paths import KB_DIR


def _table(headers: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        cells = []
        for value in row:
            if isinstance(value, float):
                cells.append(f"{value:,.2f}")
            else:
                cells.append(str(value if value is not None else "").replace("|", "/")[:120])
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def _pivot(rows: list[tuple]) -> tuple[list[str], list[tuple]]:
    """rows of (label, year, value) -> header + rows with one column per year."""
    years = sorted({r[1] for r in rows})
    labels: dict[str, dict[str, float]] = {}
    for label, year, value in rows:
        labels.setdefault(label, {})[year] = value
    out = []
    for label, by_year in sorted(labels.items(), key=lambda kv: -sum(kv[1].values())):
        out.append((label, *[by_year.get(y, 0.0) for y in years], sum(by_year.values())))
    return [""] + years + ["total"], out


def _write(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(f"# {title}\n\n_Generated {stamp} from data/store/mail.db._\n\n{body}", encoding="utf-8")


def build_kb(conn: sqlite3.Connection, slug: str, address: str) -> list[Path]:
    out_dir = KB_DIR / slug
    q = lambda sql, *p: conn.execute(sql, (slug, *p)).fetchall()  # noqa: E731
    written: list[Path] = []

    # overview
    total = q("SELECT COUNT(*), MIN(date), MAX(date) FROM messages WHERE mailbox = ?")[0]
    state = conn.execute("SELECT * FROM sync_state WHERE mailbox = ?", (slug,)).fetchone()
    topic_year = q(
        "SELECT topic, year, COUNT(*) FROM messages WHERE mailbox = ? GROUP BY 1, 2"
    )
    header, rows = _pivot([(t, y, float(n)) for t, y, n in topic_year])
    rows = [(r[0], *[int(v) for v in r[1:]]) for r in rows]
    priority = q("SELECT priority, COUNT(*) FROM messages WHERE mailbox = ? GROUP BY 1 ORDER BY 2 DESC")
    downloaded = q(
        "SELECT topic, COUNT(*), SUM(text_path IS NOT NULL) FROM messages "
        "WHERE mailbox = ? AND raw_path IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    )
    facts = [
        (name, conn.execute(f"SELECT COUNT(*) FROM {name} WHERE mailbox = ?", (slug,)).fetchone()[0])
        for name in ("transactions", "obligations", "documents", "contacts")
    ]
    body = (
        f"Mailbox **{address}** (`{slug}`): {total[0]:,} catalogued messages, "
        f"{(total[1] or '')[:10]} .. {(total[2] or '')[:10]}.\n\n"
        f"Last sync: {state['last_sync_at'] if state else 'never'}"
        f" (last UID {state['last_uid'] if state else '-'}).\n\n"
        "## Messages by topic and year\n\n" + _table(header, rows)
        + "\n## Priority\n\n" + _table(["priority", "messages"], priority)
        + "\n## Downloaded bodies\n\n" + _table(["topic", "bodies", "with text"], downloaded)
        + "\n## Extracted facts\n\n" + _table(["table", "rows"], facts)
    )
    path = out_dir / "overview.md"
    _write(path, "Mailbox overview", body)
    written.append(path)

    # spending
    by_cat = q(
        "SELECT category, year, ROUND(SUM(amount), 2) FROM v_spend WHERE mailbox = ? GROUP BY 1, 2"
    )
    by_vendor = q(
        "SELECT vendor, year, ROUND(SUM(amount), 2) FROM v_spend WHERE mailbox = ? GROUP BY 1, 2"
    )
    projects = q(
        "SELECT project, year, ROUND(SUM(amount), 2) FROM v_spend "
        "WHERE mailbox = ? AND project IS NOT NULL GROUP BY 1, 2"
    )
    biggest = q(
        "SELECT date, vendor, amount, project, substr(description, 1, 80) FROM v_spend "
        "WHERE mailbox = ? ORDER BY amount DESC LIMIT 25"
    )
    counts = q(
        "SELECT vendor, kind, COUNT(*), ROUND(SUM(amount), 2) FROM transactions "
        "WHERE mailbox = ? GROUP BY 1, 2 ORDER BY 1, 2"
    )
    h1, r1 = _pivot(by_cat)
    h2, r2 = _pivot(by_vendor)
    h3, r3 = _pivot(projects)
    body = (
        "Spend = `v_spend` (purchases minus refunds/cancellations), deduplicated by order/receipt id. "
        "Only vendors with an extractor are covered (see SCHEMA.md); card/bank alerts are excluded.\n\n"
        "## By category (USD)\n\n" + _table(h1, r1)
        + "\n## By vendor (USD)\n\n" + _table(h2, r2)
        + "\n## By project / job name (Home Depot PO field)\n\n" + _table(h3, r3)
        + "\n## 25 largest purchases\n\n" + _table(["date", "vendor", "amount", "project", "items"], biggest)
        + "\n## Transaction rows by kind\n\n" + _table(["vendor", "kind", "rows", "sum"], counts)
    )
    path = out_dir / "spending.md"
    _write(path, "Spending", body)
    written.append(path)

    # bills / obligations
    payees = q(
        "SELECT payee, kind, COUNT(*), MAX(date), "
        "(SELECT amount FROM obligations o2 WHERE o2.mailbox = o.mailbox AND o2.payee = o.payee "
        " AND o2.kind = o.kind AND o2.amount IS NOT NULL ORDER BY date DESC LIMIT 1) "
        "FROM obligations o WHERE mailbox = ? GROUP BY payee, kind ORDER BY COUNT(*) DESC LIMIT 60"
    )
    recent = q(
        "SELECT date, payee, kind, amount, due_date, description FROM obligations "
        "WHERE mailbox = ? ORDER BY date DESC LIMIT 40"
    )
    body = (
        "## Payees\n\n" + _table(["payee", "kind", "notices", "latest", "latest amount"], payees)
        + "\n## Most recent notices\n\n" + _table(["date", "payee", "kind", "amount", "due", "subject"], recent)
    )
    path = out_dir / "bills.md"
    _write(path, "Bills, statements and payments", body)
    written.append(path)

    # health
    timeline = q(
        "SELECT substr(date, 1, 10), COALESCE(from_name, from_email), subject FROM messages "
        "WHERE mailbox = ? AND topic = 'health' AND gone = 0 ORDER BY date DESC LIMIT 150"
    )
    senders = q(
        "SELECT COALESCE(MAX(from_name), from_domain), from_domain, COUNT(*), MAX(substr(date,1,10)) "
        "FROM messages WHERE mailbox = ? AND topic = 'health' GROUP BY from_domain ORDER BY 3 DESC LIMIT 30"
    )
    docs = q(
        "SELECT date, sender, title, attachments FROM documents WHERE mailbox = ? AND topic = 'health' "
        "ORDER BY date DESC LIMIT 50"
    )
    body = (
        "## Providers / senders\n\n" + _table(["sender", "domain", "messages", "latest"], senders)
        + "\n## Documents with attachments\n\n" + _table(["date", "from", "subject", "files"], docs)
        + "\n## Timeline (latest 150)\n\n" + _table(["date", "from", "subject"], timeline)
    )
    path = out_dir / "health.md"
    _write(path, "Health", body)
    written.append(path)

    # people & companies that matter
    contacts = q(
        "SELECT COALESCE(name, ''), email, main_topic, signal_count, substr(last_date, 1, 10) FROM contacts "
        "WHERE mailbox = ? AND signal_count > 0 AND main_topic <> 'noise' "
        "ORDER BY signal_count DESC LIMIT 80"
    )
    body = "Senders with the most non-noise mail.\n\n" + _table(
        ["name", "email", "topic", "signal msgs", "last"], contacts
    )
    path = out_dir / "contacts.md"
    _write(path, "Contacts", body)
    written.append(path)

    # needs attention
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    action = q(
        "SELECT substr(date, 1, 16), COALESCE(from_name, from_email), subject, priority_reason "
        "FROM messages WHERE mailbox = ? AND priority = 'action' AND gone = 0 AND date >= ? "
        "ORDER BY date DESC LIMIT 100",
        since,
    )
    body = f"Messages triaged as **action** since {since}.\n\n" + _table(
        ["date", "from", "subject", "why"], action
    )
    path = out_dir / "action.md"
    _write(path, "Needs attention (30 days)", body)
    written.append(path)
    return written
