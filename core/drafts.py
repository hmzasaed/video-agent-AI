"""Turn a meeting's approved tasks into one email draft per person.

Shared by the API (the Prepare emails button) and the agent's
``draft_task_emails`` tool, so both follow the same rules:

- a draft is only created for a saved contact — never a free-typed address;
- dismissed and already-emailed tasks are left out;
- re-drafting replaces that person's unsent drafts instead of adding more.
"""

from __future__ import annotations

from core import db
from core.mailer import build_task_email

DRAFTABLE = ("proposed", "approved", "drafted", "failed")


def draft_emails(analysis_id: str, contact_ids: list[int] | None = None) -> dict:
    """Return ``{"drafts": [...], "unassigned": int}`` for this meeting."""
    analysis = db.get_analysis(analysis_id)
    if analysis is None:
        raise LookupError("Unknown analysis id.")

    by_contact: dict[int, list[dict]] = {}
    unassigned = 0
    for task in db.list_tasks(analysis_id):
        if task["status"] not in DRAFTABLE:
            continue
        if not task["contact_id"]:
            unassigned += 1
            continue
        if contact_ids and task["contact_id"] not in contact_ids:
            continue
        by_contact.setdefault(task["contact_id"], []).append(task)

    drafts = []
    for contact_id, tasks in by_contact.items():
        contact = db.get_contact(contact_id)
        if contact is None:
            continue
        subject, body = build_task_email(contact, tasks, analysis.get("title", ""))
        db.delete_drafts(analysis_id, contact_id)
        task_ids = [task["id"] for task in tasks]
        email = db.create_email(analysis_id, contact_id, contact["email"], subject, body, task_ids)
        db.set_task_status(task_ids, "drafted")
        drafts.append({**email, "contact_name": contact["name"]})

    return {"drafts": drafts, "unassigned": unassigned}
