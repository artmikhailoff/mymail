"""Send a digest email to the same Gmail account over SMTP."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from scripts.lib.accounts import Account

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _plain_body(body_text: str, attachments: list[Path]) -> str:
    lines: list[str] = []
    text = (body_text or "").strip()
    if text:
        lines.append(text)
        lines.append("")
    if attachments:
        lines.append("Attached:")
        lines.extend(f"- {path.name}" for path in attachments)
    else:
        lines.append("No digest files were attached.")
    return "\n".join(lines) + "\n"


def _read_attachment(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"Digest attachment not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RuntimeError(f"Digest attachment is not UTF-8 text: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not read digest attachment {path}: {exc}") from exc


def send_digest_email(
    account: Account,
    *,
    subject: str,
    body_text: str,
    attachments: list[Path],
) -> None:
    """Self-send plain text plus digest attachments via Gmail SMTP (port 587, STARTTLS).

    Uses the address and app password already on ``account`` (same values as IMAP).
    Does not log or store the password.
    """
    address = (account.address or "").strip()
    password = "".join((account.password or "").split())
    if not address or not password or address == "you@gmail.com":
        raise RuntimeError(
            "SMTP needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD on the account "
            "(same app password as IMAP; copy .env.example)."
        )

    message = EmailMessage()
    message["From"] = address
    message["To"] = address
    message["Subject"] = subject
    message.set_content(_plain_body(body_text, attachments))
    for path in attachments:
        message.add_attachment(
            _read_attachment(path),
            subtype="markdown",
            filename=path.name,
        )

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(address, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Gmail SMTP authentication failed for {address} (SMTP {exc.smtp_code}). "
            "Check GMAIL_APP_PASSWORD (an app password, not the account password)."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Gmail SMTP send failed for {address}: {exc}") from exc
