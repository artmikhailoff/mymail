"""Print mailbox census stats from mailboxes/<slug>/stats.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.paths import index_path, stats_path  # noqa: E402
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
    parser.add_argument("--mailbox", default=None, help="Slug, email, or all")
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

    try:
        accounts = resolve_accounts(args.mailbox)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for account in accounts:
        idx = index_path(account.slug)
        st = stats_path(account.slug)
        print(f"=== {account.address} ({account.slug}) ===")
        if args.rebuild or not st.is_file():
            records = load_index(idx)
            if not records:
                print(f"error: no index at {idx}. Run scan_mailbox.py first.", file=sys.stderr)
                continue
            stats = aggregate(records)
            write_stats(stats, st)
        else:
            stats = json.loads(st.read_text(encoding="utf-8"))

        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            print(format_report(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
