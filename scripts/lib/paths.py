"""Filesystem layout for mymail (no SQLite).

data/
  mailboxes/<slug>/
    mailbox.json
    index.jsonl
    stats.json
    unsubscribe_log.jsonl
    delete_log.jsonl
  raw/<slug>/<topic>/<year>/<msgid>.eml(+.meta.json)
  processed/<slug>/<topic>/<year>/<msgid>.txt
  bundles/<slug>/<topic>.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
MAILBOXES_DIR = DATA_DIR / "mailboxes"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
BUNDLES_DIR = DATA_DIR / "bundles"
CREDENTIALS_DIR = REPO_ROOT / "credentials"

# Legacy flat paths (pre multi-mailbox) — migrated on first use.
LEGACY_INDEX_PATH = DATA_DIR / "index.jsonl"
LEGACY_STATS_PATH = DATA_DIR / "stats.json"
LEGACY_UNSUBSCRIBE_LOG_PATH = DATA_DIR / "unsubscribe_log.jsonl"

# Topics we download full bodies for (noise is never downloaded).
DOWNLOAD_TOPICS: tuple[str, ...] = (
    "health",
    "construction",
    "finance",
    "travel",
    "school",
    "shopping",
    "personal",
)

# Topics that may be included with --include-other / --include-accounts.
OPTIONAL_DOWNLOAD_TOPICS: tuple[str, ...] = ("accounts", "other")


def mailbox_slug(address: str) -> str:
    text = (address or "").strip().lower()
    if "@" not in text:
        raise RuntimeError(f"Invalid mailbox address: {address!r}")
    local, _, domain = text.partition("@")
    local = re.sub(r"[^a-z0-9]+", "_", local).strip("_")
    domain = re.sub(r"[^a-z0-9]+", "_", domain).strip("_")
    return f"{local}_at_{domain}"


def mailbox_dir(slug: str) -> Path:
    return MAILBOXES_DIR / slug


def index_path(slug: str) -> Path:
    return mailbox_dir(slug) / "index.jsonl"


def stats_path(slug: str) -> Path:
    return mailbox_dir(slug) / "stats.json"


def unsubscribe_log_path(slug: str) -> Path:
    return mailbox_dir(slug) / "unsubscribe_log.jsonl"


def delete_log_path(slug: str) -> Path:
    return mailbox_dir(slug) / "delete_log.jsonl"


def wipe_log_path(slug: str) -> Path:
    return mailbox_dir(slug) / "wipe_log.jsonl"


def mailbox_meta_path(slug: str) -> Path:
    return mailbox_dir(slug) / "mailbox.json"


def raw_message_path(slug: str, topic: str, year: str, msgid: str) -> Path:
    return RAW_DIR / slug / topic / year / f"{msgid}.eml"


def processed_text_path(slug: str, topic: str, year: str, msgid: str) -> Path:
    return PROCESSED_DIR / slug / topic / year / f"{msgid}.txt"


def bundle_path(slug: str, topic: str) -> Path:
    return BUNDLES_DIR / slug / f"{topic}.md"


def year_of_record(record: dict) -> str:
    text = str(record.get("internal_date") or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return "unknown"


def write_mailbox_meta(slug: str, address: str, **extra: object) -> None:
    path = mailbox_meta_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"slug": slug, "address": address, **extra}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_legacy_mailbox(slug: str, address: str) -> None:
    """Move flat data/* files into mailboxes/<slug>/ if needed."""
    dest_index = index_path(slug)
    if dest_index.is_file():
        return
    if not LEGACY_INDEX_PATH.is_file():
        return
    dest_index.parent.mkdir(parents=True, exist_ok=True)
    LEGACY_INDEX_PATH.replace(dest_index)
    if LEGACY_STATS_PATH.is_file() and not stats_path(slug).is_file():
        LEGACY_STATS_PATH.replace(stats_path(slug))
    if LEGACY_UNSUBSCRIBE_LOG_PATH.is_file() and not unsubscribe_log_path(slug).is_file():
        LEGACY_UNSUBSCRIBE_LOG_PATH.replace(unsubscribe_log_path(slug))
    write_mailbox_meta(slug, address, migrated_from="legacy_flat_data")


# Back-compat aliases used by older script imports during transition.
INDEX_PATH = LEGACY_INDEX_PATH
STATS_PATH = LEGACY_STATS_PATH
UNSUBSCRIBE_LOG_PATH = LEGACY_UNSUBSCRIBE_LOG_PATH
SAMPLES_DIR = DATA_DIR / "samples"
