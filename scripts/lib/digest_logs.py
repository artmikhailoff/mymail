"""Read delete_log.jsonl and wipe_log.jsonl for digest cleanup stats.

Timestamps
----------
Log writers store ``at`` with ``datetime.now(timezone.utc).isoformat()``
(aware, offset ``+00:00`` or ``Z``). Digest windows in ``digest.py`` use
naive local ``datetime.now()``.

``parse_log_timestamp`` makes those comparable:

- Aware ISO values (including ``+00:00`` and ``Z``) are converted to naive
  local time: ``astimezone()`` then drop ``tzinfo``.
- Naive ISO strings are left as naive local time.

Window filters compare those naive local values to ``since`` / ``until``,
inclusive on both ends. Pass naive local bounds, as ``build_digest`` does.
Aware bounds are converted the same way before comparison.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

_TARGET_ORDER = ("promotions", "updates", "spam", "trash")
_ACTION_ORDER = ("trash", "permanent_delete", "empty")


def parse_log_timestamp(entry: dict) -> datetime:
    """Return ``entry["at"]`` as a naive local datetime."""
    raw = entry.get("at")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("log entry missing 'at'")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid log timestamp: {raw!r}") from exc
    return _as_local_naive(parsed)


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file. Missing files and bad lines are skipped."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def delete_removals_in_window(
    entries: Iterable[dict],
    since: datetime,
    until: datetime,
) -> list[dict]:
    """Delete-log rows with ``status == "ok"`` inside ``[since, until]``."""
    start = _as_local_naive(since)
    end = _as_local_naive(until)
    if start > end:
        return []
    kept: list[dict] = []
    for entry in entries:
        if str(entry.get("status") or "").strip().lower() != "ok":
            continue
        if _in_window(entry, start, end):
            kept.append(entry)
    return kept


def top_senders_from_delete_log(
    entries: Iterable[dict],
    *,
    limit: int = 15,
) -> list[tuple[str, int]]:
    """Most common senders in delete-log rows.

    ``"Name <email@x.com>"`` counts as the email address. A bare from string
    is used when there is no angle-bracket address.
    """
    if limit < 1:
        return []
    counts: Counter[str] = Counter()
    for entry in entries:
        counts[_sender_label(entry.get("from"))] += 1
    return counts.most_common(limit)


def summarize_wipe_log(
    entries: Iterable[dict],
    since: datetime,
    until: datetime,
) -> list[dict]:
    """Group wipe-log rows in the window by ``target`` + ``action``.

    ``count`` is summed when present. Rows that fail timestamp parsing are
    skipped. Result order is promotions, updates, spam, trash, then any
    other target.
    """
    start = _as_local_naive(since)
    end = _as_local_naive(until)
    if start > end:
        return []
    totals: dict[tuple[str, str], int] = {}
    for entry in entries:
        if not _in_window(entry, start, end):
            continue
        target = str(entry.get("target") or "").strip()
        action = str(entry.get("action") or "").strip()
        totals[(target, action)] = totals.get((target, action), 0) + _as_count(entry)
    rows = [
        {"target": target, "action": action, "count": count}
        for (target, action), count in totals.items()
    ]
    rows.sort(key=_wipe_sort_key)
    return rows


def _as_local_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _in_window(entry: dict, since: datetime, until: datetime) -> bool:
    try:
        stamp = parse_log_timestamp(entry)
    except ValueError:
        return False
    return since <= stamp <= until


def _as_count(entry: dict) -> int:
    if "count" not in entry or entry.get("count") is None:
        return 0
    try:
        return int(entry["count"])
    except (TypeError, ValueError):
        return 0


def _sender_label(from_field: object) -> str:
    text = str(from_field or "").strip()
    if "<" in text and ">" in text:
        start = text.rfind("<")
        end = text.rfind(">")
        if start < end:
            email = text[start + 1 : end].strip().lower()
            if email:
                return email
    if text:
        return text.lower()
    return "(unknown)"


def _wipe_sort_key(row: dict) -> tuple:
    target = str(row.get("target") or "")
    action = str(row.get("action") or "")
    try:
        target_index = _TARGET_ORDER.index(target)
    except ValueError:
        target_index = len(_TARGET_ORDER)
    try:
        action_index = _ACTION_ORDER.index(action)
    except ValueError:
        action_index = len(_ACTION_ORDER)
    return (target_index, target, action_index, action)
