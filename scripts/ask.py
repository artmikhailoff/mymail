"""Read-only questions against data/store/mail.db.

  python scripts/ask.py "SELECT year, SUM(amount) FROM v_spend WHERE category='construction' GROUP BY 1"
  python scripts/ask.py schema
  python scripts/ask.py spend --category construction --year 2022
  python scripts/ask.py search "roof leak" --topic construction
  python scripts/ask.py msg 1780642320422061568

Output is a compact markdown table (cheap to read for an agent).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import db  # noqa: E402
from scripts.lib.paths import REPO_ROOT  # noqa: E402

MAX_CELL = 100


def _print_rows(cursor: sqlite3.Cursor, limit: int) -> None:
    headers = [d[0] for d in cursor.description or []]
    rows = cursor.fetchmany(limit + 1)
    if not headers:
        print("(no result set)")
        return
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in rows[:limit]:
        cells = []
        for value in row:
            if isinstance(value, float):
                text = f"{value:,.2f}"
            else:
                text = "" if value is None else str(value)
            text = text.replace("\n", " ").replace("|", "/")
            cells.append(text[:MAX_CELL] + ("…" if len(text) > MAX_CELL else ""))
        print("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        print(f"\n(truncated at {limit} rows; use --limit)")
    else:
        print(f"\n({len(rows)} row(s))")


def cmd_sql(conn: sqlite3.Connection, sql: str, limit: int) -> int:
    _print_rows(conn.execute(sql), limit)
    return 0


def cmd_schema(conn: sqlite3.Connection) -> int:
    for (name, kind) in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'message_text_%' ORDER BY type, name"
    ):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"{kind} {name} ({count:,} rows): {', '.join(cols)}")
    return 0


def cmd_spend(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    where, params = ["1=1"], []
    for column in ("category", "year", "vendor", "project"):
        value = getattr(args, column)
        if value:
            where.append(f"{column} = ?")
            params.append(str(value))
    group = args.by or "category, year"
    sql = (
        f"SELECT {group}, COUNT(*) AS n, ROUND(SUM(amount), 2) AS total "
        f"FROM v_spend WHERE {' AND '.join(where)} GROUP BY {group} ORDER BY {group}"
    )
    if args.items:
        sql = (
            "SELECT date, vendor, kind, amount, project, order_ref, substr(description,1,90) AS items "
            f"FROM v_spend WHERE {' AND '.join(where)} ORDER BY date"
        )
    _print_rows(conn.execute(sql, params), args.limit)
    return 0


def cmd_search(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    where, params = ["message_text MATCH ?"], [args.query]
    if args.topic:
        where.append("m.topic = ?")
        params.append(args.topic)
    if args.year:
        where.append("m.year = ?")
        params.append(str(args.year))
    sql = (
        "SELECT substr(m.date,1,10) AS date, COALESCE(m.from_name, m.from_email) AS sender, "
        "m.subject, m.topic, m.gmail_msgid, "
        "snippet(message_text, 3, '[', ']', '…', 12) AS snippet "
        "FROM message_text JOIN messages m "
        "ON m.mailbox = message_text.mailbox AND m.gmail_msgid = message_text.gmail_msgid "
        f"WHERE {' AND '.join(where)} ORDER BY m.date DESC"
    )
    _print_rows(conn.execute(sql, params), args.limit)
    return 0


def cmd_msg(conn: sqlite3.Connection, msgid: str, chars: int) -> int:
    row = conn.execute(
        "SELECT * FROM messages WHERE gmail_msgid = ?", (msgid,)
    ).fetchone()
    if not row:
        print(f"no message {msgid}")
        return 1
    print(f"{row['date']} | {row['from_name'] or ''} <{row['from_email']}> | {row['subject']}")
    print(f"topic={row['topic']} priority={row['priority']} raw={row['raw_path']}")
    if row["text_path"]:
        text = (REPO_ROOT / row["text_path"]).read_text(encoding="utf-8", errors="replace")
        print("-" * 60)
        print(text[:chars] + ("\n… (truncated)" if len(text) > chars else ""))
    else:
        print("(body not downloaded)")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            pass

    argv = sys.argv[1:]
    if argv and argv[0] not in {"schema", "spend", "search", "msg", "-h", "--help"}:
        argv = ["sql", *argv]

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sql", help="Run a SELECT")
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=200)
    sub.add_parser("schema", help="List tables, columns and row counts")
    p = sub.add_parser("spend", help="Spend totals from v_spend")
    p.add_argument("--category")
    p.add_argument("--year")
    p.add_argument("--vendor")
    p.add_argument("--project")
    p.add_argument("--by", help="GROUP BY columns (default: category, year)")
    p.add_argument("--items", action="store_true", help="List individual transactions")
    p.add_argument("--limit", type=int, default=500)
    p = sub.add_parser("search", help="Full-text search over downloaded bodies (FTS5 syntax)")
    p.add_argument("query")
    p.add_argument("--topic")
    p.add_argument("--year")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("msg", help="Show one message (header + text)")
    p.add_argument("msgid")
    p.add_argument("--chars", type=int, default=4000)
    args = parser.parse_args(argv)

    try:
        conn = db.connect(readonly=True)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        if args.command == "sql":
            return cmd_sql(conn, args.sql, args.limit)
        if args.command == "schema":
            return cmd_schema(conn)
        if args.command == "spend":
            return cmd_spend(conn, args)
        if args.command == "search":
            return cmd_search(conn, args)
        return cmd_msg(conn, args.msgid, args.chars)
    except sqlite3.Error as exc:
        print(f"sql error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
