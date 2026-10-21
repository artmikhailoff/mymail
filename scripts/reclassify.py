"""Re-run topic rules on data/index.jsonl without contacting Gmail."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.classify import classify_record  # noqa: E402
from scripts.lib.paths import INDEX_PATH, REPO_ROOT, STATS_PATH  # noqa: E402
from scripts.lib.stats import aggregate, format_report, load_index, write_stats  # noqa: E402


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the new report without rewriting index/stats",
    )
    args = parser.parse_args()

    records = load_index(INDEX_PATH)
    if not records:
        print(
            f"error: no index at {INDEX_PATH}. Run scripts/scan_mailbox.py first.",
            file=sys.stderr,
        )
        return 1

    updated: list[dict] = []
    changed = 0
    for record in records:
        result = classify_record(record)
        old = record.get("topic")
        row = dict(record)
        row["topic"] = result.topic
        row["reasons"] = list(result.reasons)
        row["confidence"] = result.confidence
        if old != result.topic:
            changed += 1
        updated.append(row)

    stats = aggregate(updated)
    print(f"Reclassified {len(updated)} rows ({changed} topic changes)")
    print(format_report(stats))
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    tmp = INDEX_PATH.with_suffix(INDEX_PATH.suffix + ".part")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as handle:
        for row in updated:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(INDEX_PATH)
    write_stats(stats, STATS_PATH)
    print(
        f"Wrote {INDEX_PATH.relative_to(REPO_ROOT)} "
        f"and {STATS_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
