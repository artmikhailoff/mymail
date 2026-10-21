"""Read-only Gmail IMAP helpers for mailbox census.

Opens All Mail readonly, searches year (or month) windows, and fetches
envelope metadata with BODY.PEEK fields never requested for the census path.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from scripts.lib.paths import REPO_ROOT

T = TypeVar("T")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Exclude chats/spam/trash/drafts. Keep promotions so noise can be counted.
SCOPE = "-in:chats -in:spam -in:trash -in:drafts"

META_FIELDS = [
    "X-GM-MSGID",
    "X-GM-THRID",
    "X-GM-LABELS",
    "INTERNALDATE",
    "RFC822.SIZE",
    "ENVELOPE",
]

# A single Gmail IMAP search can omit matches once the hit list gets very large.
LARGE_YEAR_HITS = 5000


def years_to_scan(since_year: int, until_year: int) -> list[int]:
    if since_year > until_year:
        raise RuntimeError(f"since-year {since_year} is after until-year {until_year}")
    return list(range(since_year, until_year + 1))


def compose_year_query(year: int, extra: str = "") -> str:
    """Gmail after/before are exclusive, so the window is exactly that calendar year."""
    base = f"after:{year - 1}/12/31 before:{year + 1}/01/01 {SCOPE}"
    return f"{base} {extra}".strip() if extra else base


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    first = datetime(year, month, 1)
    start = first - timedelta(days=1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")


def compose_month_query(year: int, month: int, extra: str = "") -> str:
    start, end = _month_bounds(year, month)
    base = f"after:{start} before:{end} {SCOPE}"
    return f"{base} {extra}".strip() if extra else base


def search_year(gmail: GmailSession, year: int, extra: str = "") -> tuple[list[int], bool]:
    from imapclient.exceptions import IMAPClientError, LoginError

    uids = gmail.search_raw(compose_year_query(year, extra))
    if len(uids) < LARGE_YEAR_HITS:
        return uids, False
    found: set[int] = set(uids)
    for month in range(1, 13):
        try:
            found.update(gmail.search_raw(compose_month_query(year, month, extra)))
        except LoginError:
            raise
        except IMAPClientError:
            continue
    return sorted(found), True


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_credentials(env_path: Path | None = None) -> tuple[str, str]:
    load_env_file(env_path or (REPO_ROOT / ".env"))
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())
    if not address or not password or address == "you@gmail.com":
        raise RuntimeError(
            "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env "
            "(copy .env.example). Gmail IMAP needs an app password from "
            "Google Account → Security → App passwords, and IMAP enabled in Gmail."
        )
    return address, password


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1", errors="replace")
    return str(value)


def _header_text(value: object) -> str:
    raw = _text(value).strip()
    if "=?" not in raw:
        return raw
    parts: list[str] = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _addrs(value: object) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for addr in value:
        name = _header_text(getattr(addr, "name", None))
        mailbox = _text(getattr(addr, "mailbox", None))
        host = _text(getattr(addr, "host", None))
        email = f"{mailbox}@{host}" if mailbox and host else mailbox or host
        if name and email:
            parts.append(f"{name} <{email}>")
        elif name or email:
            parts.append(name or email)
    return ", ".join(parts)


def _labels(value: object) -> list[str]:
    if not value:
        return []
    return [_text(item) for item in value]


def _field(item: dict, *names: str) -> object:
    for name in names:
        if name in item:
            return item[name]
        encoded = name.encode()
        if encoded in item:
            return item[encoded]
    return None


def gmail_msgid(item: dict) -> str:
    value = _field(item, "X-GM-MSGID")
    text = _text(value).strip()
    if not text.isdigit():
        raise RuntimeError(f"Gmail message id missing in IMAP response ({text!r})")
    return text


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _one_line(value: str) -> str:
    """Keep JSONL rows single-line even if a header contains control chars."""
    return (
        (value or "")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\u2028", " ")
        .replace("\u2029", " ")
    )


def envelope_record(item: dict) -> dict:
    """Build a lightweight header record for classification and the index."""
    envelope = _field(item, "ENVELOPE")
    internal = _field(item, "INTERNALDATE")
    size = _field(item, "RFC822.SIZE")
    return {
        "gmail_msgid": gmail_msgid(item),
        "thread_id": _text(_field(item, "X-GM-THRID")),
        "internal_date": _iso(internal),
        "from": _one_line(_addrs(getattr(envelope, "from_", None)) if envelope else ""),
        "to": _one_line(_addrs(getattr(envelope, "to", None)) if envelope else ""),
        "subject": _one_line(
            _header_text(getattr(envelope, "subject", None)) if envelope else ""
        ),
        "labels": _labels(_field(item, "X-GM-LABELS")),
        "size_bytes": int(size) if isinstance(size, int) else None,
    }


def describe(record: dict) -> str:
    when = record.get("internal_date") or ""
    sender = record.get("from") or ""
    subject = record.get("subject") or ""
    return f"{when} | {sender} | {subject}"


class GmailSession:
    """One readonly IMAP session. Reconnects after dropped connections."""

    def __init__(self, address: str, password: str):
        self.address = address
        self.password = password
        self.client = None
        self.folder: str | None = None
        self.exists: int | None = None

    def __enter__(self) -> GmailSession:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        from imapclient import IMAPClient

        self.close()
        client = IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, timeout=180)
        try:
            client.login(self.address, self.password)
            folder = _all_mail_folder(client)
            selected = client.select_folder(folder, readonly=True)
        except Exception:
            try:
                client.logout()
            except Exception:
                pass
            raise
        self.client = client
        self.folder = folder
        exists = selected.get(b"EXISTS", selected.get("EXISTS"))
        self.exists = int(exists) if exists is not None else None

    def close(self) -> None:
        client = self.client
        self.client = None
        if client is None:
            return
        try:
            client.logout()
        except Exception:
            try:
                client.shutdown()
            except Exception:
                pass

    def search_raw(self, query: str) -> list[int]:
        def run() -> list[int]:
            assert self.client is not None
            return [int(uid) for uid in self.client.gmail_search(query)]

        return self._call(run)

    def fetch(self, uids: list[int], fields: list[str]) -> dict:
        def run() -> dict:
            assert self.client is not None
            if not uids:
                return {}
            return dict(self.client.fetch(uids, fields))

        return self._call(run)

    def _call(self, fn: Callable[[], T]) -> T:
        from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError

        delay = 2.0
        last: BaseException | None = None
        for attempt in range(4):
            try:
                if self.client is None:
                    self.connect()
                return fn()
            except LoginError:
                raise
            except IMAPClientAbortError as exc:
                last = exc
            except IMAPClientError as exc:
                if not _transient(exc):
                    raise
                last = exc
            except (OSError, TimeoutError, ConnectionError) as exc:
                last = exc
            if attempt == 3:
                break
            self.close()
            time.sleep(delay)
            delay *= 2
        assert last is not None
        raise last


def _transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        word in text
        for word in ("socket", "eof", "timed out", "timeout", "connection", "bye", "unavailable")
    )


def _all_mail_folder(client: object) -> str:
    for flags, _delim, name in client.list_folders():  # type: ignore[attr-defined]
        normalized = {_text(flag) for flag in flags}
        if "\\All" in normalized:
            return _text(name)
    raise RuntimeError(
        "Could not find the Gmail All Mail folder. Enable IMAP in Gmail "
        "(Settings → See all settings → Forwarding and POP/IMAP)."
    )


def uid_map(fetched: dict) -> dict[int, dict]:
    return {int(uid): item for uid, item in fetched.items()}


def chunks(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
