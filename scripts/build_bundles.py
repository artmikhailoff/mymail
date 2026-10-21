"""Build per-topic markdown bundles under data/bundles/<mailbox>/ for agent @-mentions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.accounts import resolve_accounts  # noqa: E402
from scripts.lib.paths import (  # noqa: E402
    DOWNLOAD_TOPICS,
    PROCESSED_DIR,
    REPO_ROOT,
    bundle_path,
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


def _build_topic(slug: str, topic: str, *, max_chars: int) -> Path | None:
    root = PROCESSED_DIR / slug / topic
    if not root.is_dir():
        return None
    files = sorted(root.rglob("*.txt"), reverse=True)
    if not files:
        return None

    parts: list[str] = [
        f"# {topic} — {slug}",
        "",
        f"Generated from `{root.relative_to(REPO_ROOT).as_posix()}/`.",
        "",
    ]
    used = 0
    included = 0
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        chunk = f"## {path.stem}\n\nSource: `{path.relative_to(REPO_ROOT).as_posix()}`\n\n{text}\n"
        if used + len(chunk) > max_chars and included:
            parts.append(
                f"\n_… truncated; {len(files) - included} more files under "
                f"`{root.relative_to(REPO_ROOT).as_posix()}/`._\n"
            )
            break
        parts.append(chunk)
        parts.append("\n---\n")
        used += len(chunk)
        included += 1

    out = bundle_path(slug, topic)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailbox", default=None, help="Slug, email, or all")
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="Topic to bundle (repeatable). Default: download allowlist",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=400_000,
        help="Soft cap per bundle file (default: 400000)",
    )
    args = parser.parse_args()

    try:
        accounts = resolve_accounts(args.mailbox)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    topics = args.topics or list(DOWNLOAD_TOPICS)
    written = 0
    for account in accounts:
        for topic in topics:
            path = _build_topic(account.slug, topic, max_chars=args.max_chars)
            if path is None:
                print(f"  skip {account.slug}/{topic} (no processed text)")
                continue
            print(f"  {path.relative_to(REPO_ROOT)}")
            written += 1
    print(f"Built {written} bundle(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
