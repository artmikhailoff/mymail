from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
INDEX_PATH = DATA_DIR / "index.jsonl"
STATS_PATH = DATA_DIR / "stats.json"
SAMPLES_DIR = DATA_DIR / "samples"
UNSUBSCRIBE_LOG_PATH = DATA_DIR / "unsubscribe_log.jsonl"
