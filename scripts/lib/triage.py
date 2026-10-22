"""Assign every message a priority: action | fyi | archive | junk.

Rules live in config/rules.toml. Priority is recomputed for the whole catalog
on each run (cheap), so editing the rules takes effect retroactively.
"""

from __future__ import annotations

import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from scripts.lib.paths import RULES_PATH

PRIORITIES = ("action", "fyi", "archive", "junk")
FYI_TOPICS = {"health", "construction", "finance", "travel", "school"}


@dataclass
class Rules:
    vip: list[str] = field(default_factory=list)
    block: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    action_subjects: list[re.Pattern] = field(default_factory=list)
    archive_topics: set[str] = field(default_factory=set)
    archive_subjects: list[re.Pattern] = field(default_factory=list)
    action_max_age_days: int = 30
    action_excludes: list[re.Pattern] = field(default_factory=list)
    fyi_max_age_days: int = 60


def load_rules(path=RULES_PATH) -> Rules:
    if not path.is_file():
        return Rules()
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def senders(section: str) -> list[str]:
        return [s.strip().lower() for s in data.get(section, {}).get("senders", []) if s.strip()]

    def patterns(section: str) -> list[re.Pattern]:
        return [re.compile(p, re.I) for p in data.get(section, {}).get("subject_patterns", [])]

    return Rules(
        vip=senders("vip"),
        block=senders("block"),
        keep=senders("keep"),
        action_subjects=patterns("action"),
        archive_topics=set(data.get("archive", {}).get("topics", [])),
        archive_subjects=patterns("archive"),
        action_max_age_days=int(data.get("action", {}).get("max_age_days", 30)),
        action_excludes=[re.compile(p, re.I) for p in data.get("action", {}).get("exclude_patterns", [])],
        fyi_max_age_days=int(data.get("fyi", {}).get("max_age_days", 60)),
    )


def sender_matches(email: str, patterns: list[str]) -> bool:
    email = (email or "").lower()
    domain = email.rsplit("@", 1)[-1]
    for pattern in patterns:
        if "@" in pattern:
            if email == pattern:
                return True
        elif domain == pattern or domain.endswith("." + pattern):
            return True
    return False


def priority_for(
    row: sqlite3.Row | dict,
    rules: Rules,
    owner: str,
    action_cutoff: str = "",
    fyi_cutoff: str = "",
) -> tuple[str, str]:
    priority, reason = _base_priority(row, rules, owner)
    date = row["date"] or ""
    if priority == "action" and action_cutoff and date < action_cutoff:
        priority, reason = "fyi", f"{reason} (older than {rules.action_max_age_days}d)"
    if priority == "fyi" and fyi_cutoff and date < fyi_cutoff:
        priority, reason = "archive", f"{reason} (older than {rules.fyi_max_age_days}d)"
    return priority, reason


def _action_match(subject: str, rules: Rules) -> str | None:
    if any(p.search(subject) for p in rules.action_excludes):
        return None
    for pattern in rules.action_subjects:
        if pattern.search(subject):
            return f"subject~{pattern.pattern}"
    return None


def _base_priority(row: sqlite3.Row | dict, rules: Rules, owner: str) -> tuple[str, str]:
    email = (row["from_email"] or "").lower()
    subject = row["subject"] or ""
    topic = row["topic"] or "other"

    if sender_matches(email, rules.block):
        return "junk", "rules.block"
    if sender_matches(email, rules.vip):
        return "action", "rules.vip"
    if email and email == owner:
        return "archive", "sent by me"

    kept = sender_matches(email, rules.keep)
    action = _action_match(subject, rules)
    if topic == "noise":
        if not kept:
            return "junk", "topic=noise"
        if action:
            return "action", action
        return "archive", "rules.keep (classified noise)"

    if action:
        return "action", action
    for pattern in rules.archive_subjects:
        if pattern.search(subject):
            return "archive", f"subject~{pattern.pattern}"

    if topic == "personal":
        return "action", "from a person"
    if topic in FYI_TOPICS or kept:
        return "fyi", f"topic={topic}" if topic in FYI_TOPICS else "rules.keep"
    if topic in rules.archive_topics:
        return "archive", f"topic={topic}"
    return "archive", "default"


def triage_all(conn: sqlite3.Connection, rules: Rules | None = None) -> dict[str, int]:
    rules = rules or load_rules()
    owners = {
        row["slug"]: row["address"].lower()
        for row in conn.execute("SELECT slug, address FROM mailboxes")
    }
    cutoff = (datetime.now() - timedelta(days=rules.action_max_age_days)).strftime("%Y-%m-%d")
    fyi_cutoff = (datetime.now() - timedelta(days=rules.fyi_max_age_days)).strftime("%Y-%m-%d")
    updates = []
    counts = {p: 0 for p in PRIORITIES}
    for row in conn.execute(
        "SELECT mailbox, gmail_msgid, date, from_email, subject, topic, priority, priority_reason "
        "FROM messages"
    ):
        priority, reason = priority_for(
            row, rules, owners.get(row["mailbox"], ""), cutoff, fyi_cutoff
        )
        counts[priority] += 1
        if priority != row["priority"] or reason != row["priority_reason"]:
            updates.append((priority, reason, row["mailbox"], row["gmail_msgid"]))
    conn.executemany(
        "UPDATE messages SET priority = ?, priority_reason = ? WHERE mailbox = ? AND gmail_msgid = ?",
        updates,
    )
    conn.commit()
    counts["changed"] = len(updates)
    return counts
