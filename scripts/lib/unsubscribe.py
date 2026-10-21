"""Unsubscribe helpers for commercial / noise mail.

Uses List-Unsubscribe (+ optional List-Unsubscribe-Post one-click).
Does not modify the mailbox; only HTTP(S) and optional SMTP mailto.
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from scripts.lib.paths import UNSUBSCRIBE_LOG_PATH
from scripts.lib.stats import sender_key

Method = Literal["one_click", "http_get", "mailto", "none"]
Status = Literal[
    "planned",
    "ok",
    "skipped",
    "already",
    "no_header",
    "error",
]

_ANGLE = re.compile(r"<([^>]+)>")
_USER_AGENT = "mymail-unsubscribe/1.0 (+local; List-Unsubscribe)"


@dataclass(frozen=True)
class UnsubscribeTarget:
    method: Method
    value: str  # URL or mailto address (with optional query)
    one_click: bool

    def summarize(self) -> str:
        if self.method == "none":
            return "no List-Unsubscribe header"
        flag = " one-click" if self.one_click else ""
        return f"{self.method}{flag}: {self.value}"


@dataclass
class UnsubscribeResult:
    sender: str
    gmail_msgid: str
    subject: str
    status: Status
    method: Method
    detail: str
    target: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "sender": self.sender,
            "gmail_msgid": self.gmail_msgid,
            "subject": self.subject,
            "status": self.status,
            "method": self.method,
            "target": self.target,
            "detail": self.detail,
        }


def parse_list_unsubscribe(header_bytes: bytes) -> UnsubscribeTarget:
    """Pick the best unsubscribe action from raw RFC822 headers."""
    msg = message_from_bytes(header_bytes, policy=default)
    raw = msg.get("List-Unsubscribe")
    if not raw:
        return UnsubscribeTarget("none", "", False)
    post = (msg.get("List-Unsubscribe-Post") or "").strip().lower()
    one_click = "list-unsubscribe=one-click" in post.replace(" ", "")

    https_urls: list[str] = []
    mailto_urls: list[str] = []
    for match in _ANGLE.finditer(str(raw)):
        item = match.group(1).strip()
        lower = item.lower()
        if lower.startswith("mailto:"):
            mailto_urls.append(item)
        elif lower.startswith("https://") or lower.startswith("http://"):
            https_urls.append(item)

    # Prefer https; one-click POST when advertised.
    if https_urls:
        url = https_urls[0]
        if one_click:
            return UnsubscribeTarget("one_click", url, True)
        return UnsubscribeTarget("http_get", url, False)
    if mailto_urls:
        return UnsubscribeTarget("mailto", mailto_urls[0], False)
    return UnsubscribeTarget("none", "", False)


def perform_unsubscribe(
    target: UnsubscribeTarget,
    *,
    from_address: str,
    app_password: str,
    timeout: float = 30.0,
) -> tuple[Status, str]:
    """Execute one unsubscribe action. Returns (status, detail)."""
    if target.method == "none":
        return "no_header", "no List-Unsubscribe header"
    if target.method == "one_click":
        return _http_one_click(target.value, timeout=timeout)
    if target.method == "http_get":
        return _http_get(target.value, timeout=timeout)
    if target.method == "mailto":
        return _mailto(target.value, from_address=from_address, app_password=app_password)
    return "error", f"unknown method {target.method}"


def _http_one_click(url: str, *, timeout: float) -> tuple[Status, str]:
    if not url.lower().startswith("https://"):
        # One-click must be https per RFC 8058; refuse plain http.
        return "error", f"one-click refused non-https URL: {url}"
    req = Request(
        url,
        data=b"List-Unsubscribe=One-Click",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return "ok", f"POST {code}"
    except HTTPError as exc:
        # Some providers return 2xx/3xx oddly; 2xx–3xx and even 404 after unsub are common.
        if 200 <= exc.code < 400:
            return "ok", f"POST {exc.code}"
        return "error", f"POST HTTPError {exc.code}: {exc.reason}"
    except URLError as exc:
        return "error", f"POST URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — surface any network failure
        return "error", f"POST failed: {exc}"


def _http_get(url: str, *, timeout: float) -> tuple[Status, str]:
    """Fallback when List-Unsubscribe-Post is missing (landing-page style links)."""
    if url.lower().startswith("http://"):
        return "error", f"refused plain http unsubscribe URL: {url}"
    req = Request(url, method="GET", headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return "ok", f"GET {code}"
    except HTTPError as exc:
        if 200 <= exc.code < 400:
            return "ok", f"GET {exc.code}"
        return "error", f"GET HTTPError {exc.code}: {exc.reason}"
    except URLError as exc:
        return "error", f"GET URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return "error", f"GET failed: {exc}"


def _mailto(mailto_url: str, *, from_address: str, app_password: str) -> tuple[Status, str]:
    parsed = urlparse(mailto_url)
    to_addr = unquote(parsed.path)
    if not to_addr or "@" not in to_addr:
        return "error", f"bad mailto: {mailto_url}"
    qs = parse_qs(parsed.query)
    subject = (qs.get("subject") or ["unsubscribe"])[0]
    body = (qs.get("body") or [""])[0]

    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body or "")

    password = "".join(app_password.split())
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
            smtp.login(from_address, password)
            smtp.send_message(msg)
        return "ok", f"mailto sent to {to_addr}"
    except Exception as exc:  # noqa: BLE001
        return "error", f"mailto failed: {exc}"


def load_done_senders(path: Path | None = None) -> set[str]:
    """Senders we already successfully unsubscribed from (or skipped as no_header)."""
    path = path or UNSUBSCRIBE_LOG_PATH
    if not path.is_file():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") in {"ok", "no_header", "already"}:
            sender = sender_key(str(row.get("sender") or ""))
            if sender:
                done.add(sender)
    return done


def append_log(result: UnsubscribeResult, path: Path | None = None) -> None:
    path = path or UNSUBSCRIBE_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")


def noise_candidates(records: list[dict]) -> list[dict]:
    """One newest noise message per sender (ads / promos / recommendations)."""
    best: dict[str, dict] = {}
    for record in records:
        if str(record.get("topic") or "") != "noise":
            continue
        sender = sender_key(str(record.get("from") or ""))
        if not sender:
            continue
        prev = best.get(sender)
        if prev is None or str(record.get("internal_date") or "") > str(
            prev.get("internal_date") or ""
        ):
            best[sender] = record
    # Newest senders first (by their newest message date).
    return sorted(
        best.values(),
        key=lambda row: str(row.get("internal_date") or ""),
        reverse=True,
    )


def display_from(record: dict) -> str:
    raw = str(record.get("from") or "")
    name, addr = parseaddr(raw)
    return addr or sender_key(raw) or raw
