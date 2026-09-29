"""Task emails: build one message per person and send it over SMTP.

Sending only ever happens through ``POST /api/emails/<id>/send``, which the
user triggers after reviewing the draft. The agent can create drafts but has
no way to send. ``EMAIL_DRY_RUN`` (on by default) logs instead of sending.
"""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def dry_run() -> bool:
    return _flag("EMAIL_DRY_RUN", True)


def _real(value: str | None) -> bool:
    """True for a real setting, not blank or a placeholder copied from .env.example."""
    value = (value or "").strip()
    return bool(value) and not value.startswith("your_") and value != "you@example.com"


def configured() -> bool:
    return _real(os.getenv("SMTP_HOST")) and (
        _real(os.getenv("SMTP_FROM")) or _real(os.getenv("SMTP_USER"))
    ) and _real(os.getenv("SMTP_PASSWORD") or "set")


def build_task_email(contact: dict, tasks: list[dict], meeting_title: str) -> tuple[str, str]:
    """Return ``(subject, plain_text_body)`` listing this person's tasks."""
    title = meeting_title or "our recent meeting"
    first_name = (contact.get("name") or "there").split()[0]
    subject = f"Your action items from {title}"

    lines = [
        f"Hi {first_name},",
        "",
        f"Here {'is the task' if len(tasks) == 1 else f'are the {len(tasks)} tasks'} "
        f"assigned to you in {title}:",
        "",
    ]
    for number, task in enumerate(tasks, start=1):
        lines.append(f"{number}. {task['text']}")
        if task.get("due"):
            lines.append(f"   Due: {task['due']}")
        if task.get("evidence"):
            lines.append(f"   From the meeting: \"{task['evidence']}\"")
        lines.append("")
    lines += [
        "Reply to this email if anything here looks wrong.",
        "",
        "Thanks!",
    ]
    return subject, "\n".join(lines)


def _html_body(text: str) -> str:
    escaped = html.escape(text).replace("\n", "<br>\n")
    return (
        '<div style="font-family: Segoe UI, Arial, sans-serif; font-size: 15px; '
        f'line-height: 1.6; color: #0f3b38;">{escaped}</div>'
    )


def send(to_addr: str, subject: str, body: str) -> str:
    """Send one message. Returns ``"sent"`` or ``"dry-run"``; raises on failure."""
    if dry_run():
        print(f"[email dry-run] To: {to_addr} | Subject: {subject}")
        return "dry-run"

    if not configured():
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and "
            "SMTP_FROM in your .env file."
        )

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM") or user

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_addr
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(_html_body(body), subtype="html")

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                if user:
                    server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=context)
                if user:
                    server.login(user, password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError(
            "SMTP login failed. For Gmail, use an App Password (not your normal password)."
        ) from error
    except (smtplib.SMTPException, OSError) as error:
        raise RuntimeError(f"Could not send email: {error}") from error

    return "sent"
