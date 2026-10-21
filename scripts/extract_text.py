"""Extract plain text from data/raw into data/processed/<mailbox>/<topic>/<year>/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.paths import PROCESSED_DIR, RAW_DIR, REPO_ROOT  # noqa: E402
from scripts.lib.store import write_processed_text  # noqa: E402


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
        "--force",
        action="store_true",
        help="Rewrite existing .txt files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Extract at most N messages this run (0 = all)",
    )
    args = parser.parse_args()

    try:
        accounts = resolve_accounts(args.mailbox)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    slugs = {a.slug for a in accounts}
    written = 0
    skipped = 0

    if not RAW_DIR.is_dir():
        print(f"No raw mail at {RAW_DIR}. Run scripts/download_bodies.py first.")
        return 0

    for eml in sorted(RAW_DIR.rglob("*.eml")):
        # raw/<slug>/<topic>/<year>/<msgid>.eml
        try:
            rel = eml.relative_to(RAW_DIR)
            slug, topic, year, name = rel.parts[0], rel.parts[1], rel.parts[2], rel.parts[3]
        except (ValueError, IndexError):
            continue
        if slug not in slugs:
            continue
        msgid = Path(name).stem
        meta_path = eml.with_suffix(".meta.json")
        record: dict
        if meta_path.is_file():
            record = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            record = {
                "gmail_msgid": msgid,
                "topic": topic,
                "internal_date": f"{year}-01-01T00:00:00",
            }

        out = PROCESSED_DIR / slug / topic / year / f"{msgid}.txt"
        if out.is_file() and not args.force:
            skipped += 1
            continue
        try:
            path = write_processed_text(slug=slug, record=record, eml_path=eml)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {eml}: {exc}", file=sys.stderr)
            continue
        written += 1
        print(f"  {path.relative_to(REPO_ROOT)}")
        if args.limit and written >= args.limit:
            break

    print(f"Wrote {written} text file(s); skipped existing {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
