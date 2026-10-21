"""Print mailbox census stats from data/stats.json (or rebuild from index)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.paths import INDEX_PATH, STATS_PATH  # noqa: E402
from scripts.lib.stats import (  # noqa: E402
    aggregate,
    format_report,
    load_index,
    write_stats,
)


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
        "--rebuild",
        action="store_true",
        help="Rebuild stats.json from index.jsonl before printing",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw stats JSON instead of the text report",
    )
    args = parser.parse_args()

    if args.rebuild or not STATS_PATH.is_file():
        records = load_index(INDEX_PATH)
        if not records:
            print(
                f"error: no index at {INDEX_PATH}. Run scripts/scan_mailbox.py first.",
                file=sys.stderr,
            )
            return 1
        stats = aggregate(records)
        write_stats(stats, STATS_PATH)
    else:
        import json

        stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))

    if args.json:
        import json

        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(format_report(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
