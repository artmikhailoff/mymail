"""Resolve one or more Gmail accounts for mymail."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from scripts.lib.gmail import load_credentials, load_env_file
from scripts.lib.paths import (
    CREDENTIALS_DIR,
    REPO_ROOT,
    mailbox_slug,
    migrate_legacy_mailbox,
    write_mailbox_meta,
)


@dataclass(frozen=True)
class Account:
    address: str
    password: str
    slug: str
    source: str


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _account_from_values(values: dict[str, str], source: str) -> Account | None:
    address = (values.get("GMAIL_ADDRESS") or "").strip()
    password = "".join((values.get("GMAIL_APP_PASSWORD") or "").split())
    if not address or not password or address == "you@gmail.com":
        return None
    return Account(
        address=address,
        password=password,
        slug=mailbox_slug(address),
        source=source,
    )


def list_accounts() -> list[Account]:
    """Primary .env plus optional credentials/<name>.env files."""
    load_env_file(REPO_ROOT / ".env")
    found: dict[str, Account] = {}

    root_values = _parse_env_file(REPO_ROOT / ".env")
    # Prefer file values; fall back to process env (CI / already exported).
    if "GMAIL_ADDRESS" not in root_values and os.environ.get("GMAIL_ADDRESS"):
        root_values["GMAIL_ADDRESS"] = os.environ["GMAIL_ADDRESS"]
    if "GMAIL_APP_PASSWORD" not in root_values and os.environ.get("GMAIL_APP_PASSWORD"):
        root_values["GMAIL_APP_PASSWORD"] = os.environ["GMAIL_APP_PASSWORD"]
    primary = _account_from_values(root_values, str(REPO_ROOT / ".env"))
    if primary:
        found[primary.slug] = primary

    if CREDENTIALS_DIR.is_dir():
        for path in sorted(CREDENTIALS_DIR.glob("*.env")):
            account = _account_from_values(_parse_env_file(path), str(path))
            if account:
                found[account.slug] = account

    if not found:
        load_credentials()  # raises with setup instructions
    return list(found.values())


def resolve_accounts(selector: str | None = None) -> list[Account]:
    """selector: None/all → every account; slug or email → one account."""
    accounts = list_accounts()
    if not accounts:
        raise RuntimeError("No Gmail accounts configured.")

    def _prepare(account: Account) -> Account:
        migrate_legacy_mailbox(account.slug, account.address)
        write_mailbox_meta(account.slug, account.address)
        return account

    if selector is None or selector.strip().lower() in {"", "all"}:
        return [_prepare(a) for a in accounts]

    needle = selector.strip().lower()
    for account in accounts:
        if account.slug == needle or account.address.lower() == needle:
            return [_prepare(account)]
    known = ", ".join(f"{a.slug} ({a.address})" for a in accounts)
    raise RuntimeError(f"Unknown mailbox {selector!r}. Known: {known}")


def default_account() -> Account:
    return resolve_accounts(None)[0]
