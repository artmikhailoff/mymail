"""Rule-based fact extraction from downloaded message text.

Each extractor declares which messages it handles and a VERSION. A message is
processed once per (extractor, version); bump VERSION to re-run an extractor
over the archive. Facts are upserted by dedupe_key, so the same order/receipt
mailed several times is counted once.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from scripts.lib import db
from scripts.lib.paths import REPO_ROOT

FTS_BODY_CHARS = 20000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ics")
DOCUMENT_TOPICS = ("health", "finance", "construction", "school", "travel", "personal")

_MONEY = r"-?\$?\s?-?[\d,]+\.\d{2}"


def _clean(text: str) -> str:
    text = re.sub(r"&#\d+;|&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: str | None) -> float | None:
    if not value:
        return None
    negative = "-" in value
    digits = re.sub(r"[^\d.]", "", value)
    if not digits:
        return None
    amount = float(digits)
    return -amount if negative else amount


def _date_any(value: str) -> str | None:
    value = value.strip().rstrip(".,")
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


@dataclass
class Fact:
    table: str  # transactions | obligations
    values: dict


@dataclass
class Extractor:
    name: str
    version: int
    applies: Callable[[sqlite3.Row], bool]
    run: Callable[[sqlite3.Row, str], list[Fact]]


def _domain(row: sqlite3.Row) -> str:
    return (row["from_domain"] or "").lower()


def _base(row: sqlite3.Row, extractor: str) -> dict:
    return {
        "mailbox": row["mailbox"],
        "gmail_msgid": row["gmail_msgid"],
        "extractor": extractor,
    }


def _date_of(row: sqlite3.Row) -> str:
    return (row["date"] or "")[:10]


# ------------------------------------------------------------- Home Depot

_HD_POS = re.compile(
    r"\b(\d{4}) (\d{5}) (\d{5}) (\d{2}/\d{2}/\d{2}) (\d{1,2}:\d{2} [AP]M)\s+(SALE|RETURN|EXCHANGE|VOID)?"
)
_HD_TOTAL = re.compile(r"(?<!SUB)(?<!SUB )\bTOTAL\s+(" + _MONEY + ")")
_HD_JOB = re.compile(r"PO/JOB NAME:\s*(.+?)\s+(?:\d{4} PRO XTRA|PRO XTRA|Get the|RETURN POLICY|\*)")
_HD_STORE = re.compile(r"seeing you again soon\.\s+(.+?)\s+\d{3}-\d{3}-\d{4}")
_HD_PAY = re.compile(r"\b(X{6,}\d{4} (?:DEBIT|VISA|MASTERCARD|AMEX|DISCOVER|HOME DEPOT)|CASH|GIFT CARD)\b")
_HD_WEB_ORDER = re.compile(r"\b(W[A-Z]\d{8})\b")


def _hd_receipt(row: sqlite3.Row, text: str) -> list[Fact]:
    body = _clean(text)
    head = _HD_POS.search(body)
    total = _HD_TOTAL.search(body, head.end() if head else 0)
    if not total:
        return []
    amount = _money(total.group(1))
    if amount is None:
        return []
    store_no, register, txn, mdy, _clock, action = head.groups() if head else (None,) * 6
    kind = "refund" if (action == "RETURN" or amount < 0) else "purchase"
    if kind == "refund":
        amount = -abs(amount)
    date = _date_any(mdy) if mdy else None
    items = body[head.end(): total.start()] if head else ""
    items = re.sub(r"\bSUBTOTAL.*$", "", items).strip()
    job = _HD_JOB.search(body)
    store = _HD_STORE.search(body)
    pay = _HD_PAY.search(body, total.end())
    web = _HD_WEB_ORDER.search(body)
    key = (
        f"homedepot:pos:{store_no}-{register}-{txn}-{date}"
        if head
        else f"homedepot:msg:{row['gmail_msgid']}"
    )
    return [
        Fact(
            "transactions",
            {
                **_base(row, "homedepot_receipt"),
                "date": date or _date_of(row),
                "vendor": "Home Depot",
                "category": "construction",
                "kind": kind,
                "amount": round(amount, 2),
                "order_ref": web.group(1) if web else (f"{store_no}-{register}-{txn}" if head else None),
                "project": job.group(1).strip().lower() if job else None,
                "store": store.group(1).strip() if store else (store_no or None),
                "payment": pay.group(1) if pay else None,
                "description": items[:400] or None,
                "confidence": "high" if head else "medium",
                "dedupe_key": key,
            },
        )
    ]


_HD_ORDER_NO = re.compile(r"Order Number:?\s*(W[A-Z]\d{8})")
_HD_ORDER_TOTAL = re.compile(r"(?<!Sub)\bTotal \$([\d,]+\.\d{2})")
_HD_ORDER_DATE = re.compile(r"Order Date:?\s*([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})")
_HD_ORDER_JOB = re.compile(r"Job/PO Number:\s*(\S+)")
_HD_CANCEL_ITEM = re.compile(r"Internet # \d+ \$[\d,]+\.\d{2} \d+ \$([\d,]+\.\d{2})")
_HD_RETURN_ITEM = re.compile(r"Internet # \d+ \d+ \$([\d,]+\.\d{2})")


def _hd_online(row: sqlite3.Row, text: str) -> list[Fact]:
    body = _clean(text)
    subject = row["subject"] or ""
    number = _HD_ORDER_NO.search(body)
    if not number:
        return []
    order = number.group(1)
    when = _HD_ORDER_DATE.search(body)
    date = (_date_any(when.group(1)) if when else None) or _date_of(row)
    base = {
        **_base(row, "homedepot_online"),
        "date": date,
        "vendor": "Home Depot",
        "category": "construction",
        "order_ref": order,
        "confidence": "high",
    }
    if re.search(r"received your order", subject, re.I):
        total = _HD_ORDER_TOTAL.search(body)
        job = _HD_ORDER_JOB.search(body)
        if not total:
            return []
        return [Fact("transactions", {
            **base, "kind": "purchase", "amount": _money(total.group(1)),
            "project": job.group(1).lower() if job else None,
            "description": body[body.find("All items"):][:400] or None,
            "dedupe_key": f"homedepot:order:{order}",
        })]
    if re.search(r"cancel", subject, re.I):
        items = [_money(m) for m in _HD_CANCEL_ITEM.findall(body)]
        amount = sum(a for a in items if a)
        if not amount:
            return []
        return [Fact("transactions", {
            **base, "kind": "refund", "amount": -round(amount, 2),
            "description": "online order cancellation",
            "dedupe_key": f"homedepot:cancel:{order}:{row['gmail_msgid']}",
        })]
    if re.search(r"returned products|refund", subject, re.I):
        items = [_money(m) for m in _HD_RETURN_ITEM.findall(body)]
        amount = sum(a for a in items if a)
        if not amount:
            return []
        return [Fact("transactions", {
            **base, "kind": "refund", "amount": -round(amount, 2),
            "description": "online order return",
            "dedupe_key": f"homedepot:return:{order}:{row['gmail_msgid']}",
        })]
    return []


# ----------------------------------------------------------------- Amazon

_AMZ_ORDER = re.compile(r"Order #:?\s*([D\d]\d{2}-\d{7}-\d{7})")
_AMZ_TOTAL = re.compile(r"(?:Order Grand Total|Grand Total|Order Total|Total):\s*\$([\d,]+\.\d{2})")
_AMZ_SKIP = re.compile(r"shipped|delivered|arriving|delivery|refund|return|cancel", re.I)


def _amazon(row: sqlite3.Row, text: str) -> list[Fact]:
    body = _clean(text)
    order = _AMZ_ORDER.search(body)
    total = _AMZ_TOTAL.search(body)
    if not order or not total:
        return []
    digital = order.group(1).startswith("D")
    return [Fact("transactions", {
        **_base(row, "amazon_order"),
        "date": _date_of(row),
        "vendor": "Amazon",
        "category": "digital" if digital else "shopping",
        "kind": "purchase",
        "amount": _money(total.group(1)),
        "order_ref": order.group(1),
        "description": (row["subject"] or "")[:200],
        "confidence": "high",
        "dedupe_key": f"amazon:order:{order.group(1)}",
    })]


# ------------------------------------------------------------------- Ally

_ALLY_AMOUNT = re.compile(r"Reference #:.*?\$([\d,]+\.\d{2})")
_ALLY_DATE = re.compile(r"Date:\s*(\d{2}/\d{2}/\d{4})")
_ALLY_TXN = re.compile(r"Transaction:\s*(.+?)\s+Date:")
_ALLY_ACCT = re.compile(r"Account Ending In:\s*(\d{4})")


def _ally_alert(row: sqlite3.Row, text: str) -> list[Fact]:
    body = _clean(text)
    subject = row["subject"] or ""
    amount = _ALLY_AMOUNT.search(body)
    if not amount:
        return []
    deposit = bool(re.search(r"deposit", subject, re.I))
    when = _ALLY_DATE.search(body)
    txn = _ALLY_TXN.search(body)
    acct = _ALLY_ACCT.search(body)
    value = _money(amount.group(1)) or 0.0
    return [Fact("transactions", {
        **_base(row, "ally_alert"),
        "date": (_date_any(when.group(1)) if when else None) or _date_of(row),
        "vendor": "Ally Bank",
        "category": "banking",
        "kind": "bank_deposit" if deposit else "bank_debit",
        "amount": value if deposit else -value,
        "payment": f"Ally ...{acct.group(1)}" if acct else None,
        "description": txn.group(1)[:200] if txn else subject[:200],
        "confidence": "high",
        "dedupe_key": f"ally:msg:{row['gmail_msgid']}",
    })]


# ------------------------------------------------------- bills / payments

_AMOUNT_DUE = re.compile(
    r"(?:Amount due|Amount Due|Total amount due|Minimum payment due|Payment amount|Amount)\s*:?\s*\$([\d,]+\.\d{2})"
)
_DUE_DATE = re.compile(
    r"(?:Due date|Payment due date|due on|Due by|Effective date)\s*:?\s*"
    r"([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
)
_ACCOUNT = re.compile(r"Account\s+(.{3,60}?\(\.\.\.\d{4}\))")
_OBLIGATION_SUBJECT = re.compile(
    r"bill|statement|payment|invoice|amount due|autopay|premium|escrow|property tax", re.I
)


def _obligation_kind(subject: str) -> str:
    s = subject.lower()
    if "statement" in s:
        return "statement"
    if "scheduled" in s or "autopay" in s or "automatic" in s:
        return "payment_scheduled"
    if re.search(r"received your .*payment|payment (received|posted)|thank you for your payment|we paid", s):
        return "payment_made"
    return "bill"


def _obligation(row: sqlite3.Row, text: str) -> list[Fact]:
    body = _clean(text)
    subject = row["subject"] or ""
    amount = _AMOUNT_DUE.search(body)
    due = _DUE_DATE.search(body)
    account = _ACCOUNT.search(body)
    kind = _obligation_kind(subject)
    if kind == "bill" and not amount:
        return []
    return [Fact("obligations", {
        **_base(row, "obligation"),
        "date": _date_of(row),
        "payee": row["from_name"] or _domain(row),
        "kind": kind,
        "amount": _money(amount.group(1)) if amount else None,
        "due_date": _date_any(due.group(1)) if due else None,
        "account": account.group(1) if account else None,
        "description": subject[:200],
        "dedupe_key": f"obligation:{row['mailbox']}:{row['gmail_msgid']}",
    })]


EXTRACTORS: list[Extractor] = [
    Extractor(
        "homedepot_receipt", 1,
        lambda r: _domain(r).endswith("homedepot.com") and "receipt" in (r["subject"] or "").lower(),
        _hd_receipt,
    ),
    Extractor(
        "homedepot_online", 1,
        lambda r: _domain(r).endswith("homedepot.com")
        and bool(re.search(r"received your order|cancel|returned products|refund", r["subject"] or "", re.I)),
        _hd_online,
    ),
    Extractor(
        "amazon_order", 1,
        lambda r: _domain(r).endswith("amazon.com")
        and bool(re.search(r"amazon\.com order", r["subject"] or "", re.I))
        and not _AMZ_SKIP.search(r["subject"] or ""),
        _amazon,
    ),
    Extractor(
        "ally_alert", 1,
        lambda r: _domain(r).endswith("ally.com")
        and bool(re.search(r"^A (recent )?debit|deposit (just hit|was made)", r["subject"] or "", re.I)),
        _ally_alert,
    ),
    Extractor(
        "obligation", 1,
        lambda r: r["topic"] == "finance"
        and bool(_OBLIGATION_SUBJECT.search(r["subject"] or ""))
        and not _domain(r).endswith(("homedepot.com", "amazon.com")),
        _obligation,
    ),
]


# ----------------------------------------------------------------- runner


def _insert_fact(conn: sqlite3.Connection, fact: Fact) -> None:
    if fact.table == "transactions":
        fact.values["year"] = (fact.values.get("date") or "")[:4] or None
    columns = list(fact.values)
    conn.execute(
        f"INSERT INTO {fact.table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(dedupe_key) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in columns if c != "dedupe_key"),
        [fact.values[c] for c in columns],
    )


def _read_text(row: sqlite3.Row) -> str | None:
    path = REPO_ROOT / row["text_path"]
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _body_only(text: str) -> str:
    _, _, body = text.partition("\n\n")
    return body or text


def run_extractors(
    conn: sqlite3.Connection, *, force: bool = False, log: Callable[[str], None] = print
) -> dict[str, int]:
    done: set[tuple[str, str, str, int]] = set()
    if not force:
        done = {
            (r[0], r[1], r[2], r[3])
            for r in conn.execute("SELECT mailbox, gmail_msgid, extractor, version FROM extraction_log")
        }
    rows = conn.execute(
        "SELECT * FROM messages WHERE text_path IS NOT NULL ORDER BY date"
    ).fetchall()
    stats: dict[str, int] = {}
    stamp = db.now_iso()
    pending_log = []
    for row in rows:
        mine = [
            e for e in EXTRACTORS
            if (row["mailbox"], row["gmail_msgid"], e.name, e.version) not in done and e.applies(row)
        ]
        index_fts = (row["mailbox"], row["gmail_msgid"], "fts", 1) not in done
        if not mine and not index_fts:
            continue
        text = _read_text(row)
        if text is None:
            continue
        for extractor in mine:
            try:
                facts = extractor.run(row, text)
                for fact in facts:
                    _insert_fact(conn, fact)
                result = "ok" if facts else "none"
                stats[extractor.name] = stats.get(extractor.name, 0) + len(facts)
            except Exception as exc:  # noqa: BLE001
                result = "error"
                log(f"  {extractor.name} failed on {row['gmail_msgid']}: {exc}")
            pending_log.append((row["mailbox"], row["gmail_msgid"], extractor.name, extractor.version, result, stamp))
        if index_fts:
            conn.execute(
                "DELETE FROM message_text WHERE mailbox = ? AND gmail_msgid = ?",
                (row["mailbox"], row["gmail_msgid"]),
            )
            conn.execute(
                "INSERT INTO message_text(mailbox, gmail_msgid, subject, body) VALUES (?, ?, ?, ?)",
                (row["mailbox"], row["gmail_msgid"], row["subject"] or "",
                 _clean(_body_only(text))[:FTS_BODY_CHARS]),
            )
            pending_log.append((row["mailbox"], row["gmail_msgid"], "fts", 1, "ok", stamp))
            stats["fts"] = stats.get("fts", 0) + 1
    conn.executemany(
        "INSERT OR REPLACE INTO extraction_log(mailbox, gmail_msgid, extractor, version, result, ran_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        pending_log,
    )
    conn.commit()
    _link_pickup_receipts(conn)
    rebuild_documents(conn)
    rebuild_contacts(conn)
    conn.commit()
    return stats


def _link_pickup_receipts(conn: sqlite3.Connection) -> None:
    """A store receipt that references a web order already counted is not new spend."""
    conn.execute(
        """
        UPDATE transactions SET kind = 'pickup_receipt'
        WHERE extractor = 'homedepot_receipt' AND kind = 'purchase'
          AND order_ref IN (
              SELECT order_ref FROM transactions
              WHERE extractor = 'homedepot_online' AND kind = 'purchase'
          )
        """
    )


def rebuild_documents(conn: sqlite3.Connection) -> None:
    marks = ",".join("?" for _ in DOCUMENT_TOPICS)
    rows = conn.execute(
        f"SELECT * FROM messages WHERE attachments IS NOT NULL AND topic IN ({marks})",
        DOCUMENT_TOPICS,
    ).fetchall()
    conn.execute("DELETE FROM documents")
    for row in rows:
        names = [n.strip() for n in (row["attachments"] or "").split(",") if n.strip()]
        docs = [n for n in names if not n.lower().endswith(IMAGE_EXT)]
        if not docs:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO documents(mailbox, gmail_msgid, date, topic, sender, title, "
            "attachments, raw_path, text_path, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row["mailbox"], row["gmail_msgid"], _date_of(row), row["topic"],
                row["from_name"] or row["from_email"], row["subject"], ", ".join(docs),
                row["raw_path"], row["text_path"], f"doc:{row['mailbox']}:{row['gmail_msgid']}",
            ),
        )


def rebuild_contacts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM contacts")
    conn.execute(
        """
        INSERT INTO contacts(mailbox, email, name, domain, message_count, signal_count, first_date, last_date)
        SELECT mailbox, from_email, MAX(from_name), MAX(from_domain), COUNT(*),
               SUM(topic <> 'noise'), MIN(date), MAX(date)
        FROM messages WHERE from_email IS NOT NULL AND from_email <> ''
        GROUP BY mailbox, from_email
        """
    )
    conn.execute(
        """
        UPDATE contacts SET main_topic = (
            SELECT topic FROM messages m
            WHERE m.mailbox = contacts.mailbox AND m.from_email = contacts.email
            GROUP BY topic ORDER BY COUNT(*) DESC LIMIT 1
        )
        """
    )


def summarize(conn: sqlite3.Connection) -> Iterable[str]:
    for row in conn.execute(
        "SELECT extractor, kind, COUNT(*), ROUND(SUM(amount), 2) FROM transactions GROUP BY 1, 2 ORDER BY 1, 2"
    ):
        yield f"  {row[0]:<20} {row[1]:<16} {row[2]:>6}  ${row[3] or 0:,.2f}"
