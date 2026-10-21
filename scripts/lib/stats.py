"""Aggregate classified index records into census stats."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.lib.classify import TOPICS


def year_of(internal_date: str) -> str:
    text = (internal_date or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return "unknown"


def sender_key(from_addr: str) -> str:
    text = (from_addr or "").strip()
    if "<" in text and ">" in text:
        start = text.rfind("<")
        end = text.rfind(">")
        if start < end:
            return text[start + 1 : end].strip().lower()
    return text.lower()


def aggregate(records: Iterable[dict]) -> dict[str, Any]:
    by_topic: Counter[str] = Counter()
    by_year: dict[str, Counter[str]] = defaultdict(Counter)
    by_confidence: Counter[str] = Counter()
    top_senders: dict[str, Counter[str]] = defaultdict(Counter)
    samples: dict[str, list[dict]] = defaultdict(list)
    total = 0

    for record in records:
        topic = str(record.get("topic") or "other")
        year = year_of(str(record.get("internal_date") or ""))
        confidence = str(record.get("confidence") or "low")
        sender = sender_key(str(record.get("from") or ""))
        by_topic[topic] += 1
        by_year[year][topic] += 1
        by_confidence[confidence] += 1
        if sender:
            top_senders[topic][sender] += 1
        if len(samples[topic]) < 8:
            samples[topic].append(
                {
                    "internal_date": record.get("internal_date"),
                    "from": record.get("from"),
                    "subject": record.get("subject"),
                    "reasons": record.get("reasons"),
                }
            )
        total += 1

    signal_topics = [
        "health",
        "construction",
        "finance",
        "travel",
        "school",
        "shopping",
        "accounts",
        "personal",
    ]
    noise = by_topic.get("noise", 0)
    other = by_topic.get("other", 0)
    signal = sum(by_topic.get(topic, 0) for topic in signal_topics)

    top_by_topic = {
        topic: [
            {"sender": sender, "count": count}
            for sender, count in counter.most_common(15)
        ]
        for topic, counter in top_senders.items()
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "summary": {
            "noise": noise,
            "signal": signal,
            "other": other,
            "noise_pct": round(100.0 * noise / total, 1) if total else 0.0,
            "signal_pct": round(100.0 * signal / total, 1) if total else 0.0,
            "other_pct": round(100.0 * other / total, 1) if total else 0.0,
        },
        "by_topic": {topic: by_topic.get(topic, 0) for topic in TOPICS},
        "by_confidence": dict(by_confidence),
        "by_year": {
            year: {topic: counts.get(topic, 0) for topic in TOPICS}
            for year, counts in sorted(by_year.items())
        },
        "top_senders": top_by_topic,
        "samples": dict(samples),
    }


def load_index(path: Path) -> list[dict]:
    """Load JSONL, stitching rare rows that were split by raw newlines in headers."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        piece = raw.strip()
        if not piece and not pending:
            continue
        candidate = f"{pending}{piece}" if pending else piece
        try:
            rows.append(json.loads(candidate))
            pending = ""
        except json.JSONDecodeError:
            pending = candidate
    if pending:
        raise RuntimeError(
            f"Corrupt index line in {path}: could not parse trailing fragment "
            f"({pending[:120]!r}…)"
        )
    return rows


def write_stats(stats: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_report(stats: dict[str, Any]) -> str:
    total = int(stats.get("total") or 0)
    summary = stats.get("summary") or {}
    lines: list[str] = []
    lines.append(f"Mailbox census — {total} messages")
    lines.append(
        f"  noise  {summary.get('noise', 0):>6}  ({summary.get('noise_pct', 0)}%)"
    )
    lines.append(
        f"  signal {summary.get('signal', 0):>6}  ({summary.get('signal_pct', 0)}%)"
    )
    lines.append(
        f"  other  {summary.get('other', 0):>6}  ({summary.get('other_pct', 0)}%)"
    )
    lines.append("")
    lines.append("By topic:")
    by_topic = stats.get("by_topic") or {}
    for topic in TOPICS:
        count = int(by_topic.get(topic, 0))
        pct = round(100.0 * count / total, 1) if total else 0.0
        lines.append(f"  {topic:<14} {count:>6}  ({pct}%)")

    by_year = stats.get("by_year") or {}
    if by_year:
        lines.append("")
        lines.append("By year (totals):")
        for year, counts in by_year.items():
            year_total = sum(int(v) for v in counts.values())
            noise = int(counts.get("noise", 0))
            lines.append(f"  {year}: {year_total} total, {noise} noise")

    top_senders = stats.get("top_senders") or {}
    for topic in ("health", "construction", "finance", "personal", "noise"):
        rows = top_senders.get(topic) or []
        if not rows:
            continue
        lines.append("")
        lines.append(f"Top senders — {topic}:")
        for row in rows[:8]:
            lines.append(f"  {row['count']:>4}  {row['sender']}")

    return "\n".join(lines) + "\n"
