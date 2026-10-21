"""Save raw .eml/.meta and extract plain text for agent browsing."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.policy import default
from pathlib import Path

from scripts.lib.paths import REPO_ROOT, processed_text_path, raw_message_path, year_of_record


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def save_raw_message(
    *,
    slug: str,
    record: dict,
    body: bytes,
) -> Path:
    topic = str(record.get("topic") or "other")
    year = year_of_record(record)
    msgid = str(record["gmail_msgid"])
    eml_path = raw_message_path(slug, topic, year, msgid)
    meta_path = eml_path.with_suffix(".meta.json")
    meta = dict(record)
    meta["mailbox"] = slug
    meta["saved_at"] = datetime.now(timezone.utc).isoformat()
    try:
        meta["path"] = eml_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        meta["path"] = eml_path.as_posix()
    _atomic_write_bytes(
        meta_path,
        (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _atomic_write_bytes(eml_path, body)
    return eml_path


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _walk_text(msg: Message) -> tuple[str, list[str]]:
    """Return (body_text, attachment_names)."""
    texts: list[str] = []
    attachments: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get_content_disposition() or "").lower()
            filename = part.get_filename()
            if filename:
                attachments.append(_decode_header_value(filename))
            if disp == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        payload = payload.decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                if isinstance(payload, str) and payload.strip():
                    texts.append(payload.strip())
            elif ctype == "text/html" and not texts:
                try:
                    html = part.get_content()
                except Exception:
                    raw = part.get_payload(decode=True)
                    html = (
                        raw.decode(part.get_content_charset() or "utf-8", errors="replace")
                        if isinstance(raw, bytes)
                        else ""
                    )
                if isinstance(html, str) and html.strip():
                    texts.append(_html_to_text(html))
    else:
        ctype = msg.get_content_type()
        try:
            payload = msg.get_content()
        except Exception:
            raw = msg.get_payload(decode=True)
            payload = (
                raw.decode(msg.get_content_charset() or "utf-8", errors="replace")
                if isinstance(raw, bytes)
                else ""
            )
        if isinstance(payload, str) and payload.strip():
            if ctype == "text/html":
                texts.append(_html_to_text(payload))
            else:
                texts.append(payload.strip())

    return "\n\n".join(texts).strip(), attachments


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = _TAG.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WS.sub(" ", text).strip()


def extract_text_from_eml(eml_path: Path, meta: dict | None = None) -> str:
    raw = eml_path.read_bytes()
    msg = message_from_bytes(raw, policy=default)
    body, attachments = _walk_text(msg)
    meta = meta or {}
    lines = [
        f"From: {meta.get('from') or _decode_header_value(msg.get('From'))}",
        f"To: {meta.get('to') or _decode_header_value(msg.get('To'))}",
        f"Date: {meta.get('internal_date') or msg.get('Date') or ''}",
        f"Subject: {meta.get('subject') or _decode_header_value(msg.get('Subject'))}",
        f"Topic: {meta.get('topic') or ''}",
        f"Gmail-MsgId: {meta.get('gmail_msgid') or eml_path.stem}",
    ]
    if attachments:
        lines.append("Attachments: " + ", ".join(attachments))
    lines.append("")
    lines.append(body or "(no text body)")
    lines.append("")
    return "\n".join(lines)


def write_processed_text(
    *,
    slug: str,
    record: dict,
    eml_path: Path,
) -> Path:
    topic = str(record.get("topic") or "other")
    year = year_of_record(record)
    msgid = str(record["gmail_msgid"])
    out = processed_text_path(slug, topic, year, msgid)
    text = extract_text_from_eml(eml_path, record)
    _atomic_write_text(out, text)
    return out
