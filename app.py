"""Flask backend for the AI Video Assistant.

Analysis runs in a background thread because transcription can take several
minutes; the browser starts a job, then polls it for progress and results.
Finished analyses are persisted to SQLite (``core/db.py``) and power the
workspace agent and the meeting task/email features.
"""

import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

# Loaded here explicitly rather than relying on an import side effect elsewhere.
load_dotenv()

from utils.audio_processor import process_input
from core.transcriber import transcribe_all, Cancelled
from core.summarize import summarize_transcript
from core.extractor import extract_information
from core.vector_store import index_video, has_index
from core.rag_engine import answer_question
from core.meeting import meeting_minutes, extract_tasks, match_tasks
from core.agent import run_agent, compare_videos
from core.drafts import draft_emails
from core import db, mailer

app = Flask(__name__)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

UPLOAD_DIR = os.path.join("downloads", "uploads")
UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".ogg"}

db.init()

# job_id -> job dict. In-memory only while running; finished jobs are persisted.
JOBS = {}
JOBS_LOCK = threading.Lock()

# Finished jobs are dropped from memory after this long to keep it bounded.
JOB_TTL = timedelta(hours=2)
MAX_JOBS = 50

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# Percentage each stage has completed by the time it finishes.
STAGE_WEIGHTS = {
    "download": 15, "transcribe": 70, "summarize": 88, "extract": 96, "index": 100,
}
MEETING_STAGE_WEIGHTS = {
    "download": 12, "transcribe": 62, "summarize": 80, "extract": 88, "tasks": 94, "index": 100,
}

MAX_QUESTION_CHARS = 1000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# At most this many emails sent per rolling minute, whatever the client does.
SEND_LIMIT_PER_MINUTE = 20
_send_times = deque()
_send_lock = threading.Lock()


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _update(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _is_cancelled(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _prune_jobs() -> None:
    """Drop finished jobs that are old, and cap total retained jobs."""
    cutoff = datetime.now() - JOB_TTL
    with JOBS_LOCK:
        stale = [
            job_id
            for job_id, job in JOBS.items()
            if job["status"] != "running"
            and datetime.fromisoformat(job["started_at"]) < cutoff
        ]
        for job_id in stale:
            del JOBS[job_id]

        if len(JOBS) > MAX_JOBS:
            finished = sorted(
                (job for job in JOBS.values() if job["status"] != "running"),
                key=lambda job: job["started_at"],
            )
            for job in finished[: len(JOBS) - MAX_JOBS]:
                JOBS.pop(job["id"], None)


def _index_for_qa(job_id: str, sections: dict):
    """Embed the results for Q&A. Returns ``(ready, error)``.

    A failure here must not throw away a finished analysis, so it is reported
    on the job instead of failing it.
    """
    with JOBS_LOCK:
        title = ((JOBS.get(job_id) or {}).get("metadata") or {}).get("title", "")

    try:
        count = index_video(job_id, sections, title=title or "")
        print(f"[{job_id[:8]}] Indexed {count} chunk(s) for Q&A.")
        return count > 0, None if count else "There was no text to index."
    except Exception as error:
        traceback.print_exc()
        return False, f"Q&A indexing failed: {error or error.__class__.__name__}"


def _persist(job_id: str, tasks: list) -> list:
    """Write the finished analysis (and meeting tasks) to SQLite.

    Returns the stored tasks with their ids. A database failure is logged but
    never fails an otherwise finished analysis.
    """
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return []
    try:
        db.save_analysis(job_id, **job)
        return db.replace_tasks(job_id, tasks) if job.get("kind") == "meeting" else []
    except Exception:
        traceback.print_exc()
        return tasks


def _run_analysis(job_id: str, source: str, kind: str = "video", title: str = "") -> None:
    weights = MEETING_STAGE_WEIGHTS if kind == "meeting" else STAGE_WEIGHTS

    def step(stage: str, message: str, percent: int) -> None:
        print(f"[{job_id[:8]}] {message}")
        _update(job_id, stage=stage, message=message, percent=percent)

    def checkpoint() -> None:
        if _is_cancelled(job_id):
            raise Cancelled("Cancelled by the user.")

    def on_metadata(meta: dict) -> None:
        # Uploads are saved under random names, so prefer the name the user gave.
        if title:
            meta = {**meta, "title": title}
        _update(job_id, metadata=meta)

    try:
        checkpoint()
        step("download", "Downloading and preparing audio...", 5)
        chunks = process_input(source, on_metadata=on_metadata)

        checkpoint()
        step(
            "transcribe",
            f"Transcribing {len(chunks)} audio chunk(s) with Whisper...",
            weights["download"],
        )

        def transcribe_progress(message: str, done: int, total: int) -> None:
            span = weights["transcribe"] - weights["download"]
            percent = weights["download"] + int(span * done / max(total, 1))
            _update(job_id, message=message, percent=percent)

        transcript = transcribe_all(
            chunks,
            model_name=WHISPER_MODEL,
            progress=transcribe_progress,
            should_cancel=lambda: _is_cancelled(job_id),
        )

        if not transcript.strip():
            raise RuntimeError(
                "No speech was detected in this video, so there is nothing to analyze."
            )

        _update(job_id, transcript=transcript)

        checkpoint()
        step("summarize", "Writing the detailed summary...", weights["transcribe"])
        summary = summarize_transcript(
            transcript,
            progress=lambda msg: _update(job_id, message=msg),
            should_cancel=lambda: _is_cancelled(job_id),
        )
        _update(job_id, summary=summary)

        minutes = ""
        if kind == "meeting":
            checkpoint()
            _update(job_id, message="Writing the meeting minutes...")
            minutes = meeting_minutes(transcript)
            _update(job_id, minutes=minutes)

        checkpoint()
        step(
            "extract",
            "Extracting action items, decisions, and questions...",
            weights["summarize"],
        )
        information = extract_information(summary)
        _update(
            job_id,
            action_items=information.get("action_items", ""),
            decisions=information.get("decisions", ""),
            questions=information.get("questions", ""),
        )

        tasks = []
        if kind == "meeting":
            checkpoint()
            step("tasks", "Assigning tasks to the people mentioned...", weights["extract"])
            extracted = extract_tasks(transcript, minutes)
            tasks = match_tasks(extracted["tasks"], db.list_contacts())
            _update(job_id, people=extracted["people"])

        checkpoint()
        step("index", "Indexing the video for Q&A...", weights.get("tasks", weights["extract"]))
        qa_ready, qa_error = _index_for_qa(job_id, {
            "transcript": transcript,
            "summary": summary,
            "minutes": minutes,
            "action_items": information.get("action_items", ""),
            "decisions": information.get("decisions", ""),
            "questions": information.get("questions", ""),
        })

        _update(
            job_id,
            qa_ready=qa_ready,
            qa_error=qa_error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        # Persist before flipping to "done", so a client never sees a finished
        # job without its tasks.
        stored_tasks = _persist(job_id, tasks)
        _update(
            job_id,
            status="done",
            stage="done",
            message="Analysis complete.",
            percent=100,
            tasks=stored_tasks,
        )

    except Cancelled:
        print(f"[{job_id[:8]}] Cancelled.")
        _update(
            job_id,
            status="cancelled",
            stage="cancelled",
            message="Analysis cancelled.",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    except Exception as error:  # surfaced to the browser as job.error
        traceback.print_exc()
        _update(
            job_id,
            status="error",
            stage="error",
            percent=100,
            error=str(error) or error.__class__.__name__,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )


def _stored_job(analysis_id: str):
    """A finished analysis from SQLite, shaped like an in-memory job."""
    analysis = db.get_analysis(analysis_id)
    if analysis is None:
        return None
    return {
        **analysis,
        "status": "done", "stage": "done", "message": "Analysis complete.",
        "percent": 100, "qa_error": None, "error": None, "cancel_requested": False,
        "people": [],
        "tasks": db.list_tasks(analysis_id) if analysis["kind"] == "meeting" else [],
    }


# ── Pages ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── Analysis jobs ────────────────────────────────────────────────────
@app.post("/api/upload")
def upload():
    """Save an uploaded recording and return a path to pass to /api/analyze."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return _error("Choose an audio or video file to upload.", 400)

    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".") for e in UPLOAD_EXTENSIONS))
        return _error(f"Unsupported file type. Use one of: {allowed}.", 400)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{extension}"))
    file.save(path)
    name = os.path.splitext(os.path.basename(file.filename))[0][:200]
    return jsonify({"path": path, "name": name}), 201


@app.errorhandler(413)
def too_large(_error_):
    return _error(f"That file is larger than the {MAX_UPLOAD_MB} MB limit.", 413)


@app.post("/api/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    source = (data.get("url") or "").strip()
    kind = "meeting" if data.get("kind") == "meeting" else "video"
    title = str(data.get("title") or "").strip()[:200]

    if not source:
        return _error("A video URL or local file path is required.", 400)

    _prune_jobs()
    job_id = uuid.uuid4().hex

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "source": source,
            "status": "running",
            "stage": "queued",
            "message": "Queued...",
            "percent": 0,
            "metadata": None,
            "transcript": "",
            "summary": "",
            "minutes": "",
            "action_items": "",
            "decisions": "",
            "questions": "",
            "people": [],
            "tasks": [],
            "qa_ready": False,
            "qa_error": None,
            "error": None,
            "cancel_requested": False,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
        }

    threading.Thread(
        target=_run_analysis, args=(job_id, source, kind, title), daemon=True
    ).start()

    return jsonify({"job_id": job_id}), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        snapshot = dict(job) if job else None

    if snapshot is None:
        snapshot = _stored_job(job_id)
    if snapshot is None:
        return _error("Unknown job id.", 404)

    return jsonify(snapshot)


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    """Ask a running job to stop.

    Cancellation is cooperative: the worker checks between stages and between
    audio chunks, so an in-flight Whisper chunk finishes before it takes effect.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return _error("Unknown job id.", 404)
        if job["status"] != "running":
            return _error(f"Job is already {job['status']}.", 409)
        job["cancel_requested"] = True
        job["message"] = "Cancelling after the current step..."

    return jsonify({"status": "cancelling"}), 202


@app.post("/api/jobs/<job_id>/ask")
def ask(job_id: str):
    """Answer a question about a finished analysis from its indexed content.

    Body: ``{"question": str, "history": [{"role", "content"}, ...]}``.
    Works for jobs pruned from memory too, as long as their index persists.
    """
    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []

    if not question:
        return _error("A question is required.", 400)
    if len(question) > MAX_QUESTION_CHARS:
        return _error(f"Questions are limited to {MAX_QUESTION_CHARS} characters.", 400)

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        status = job["status"] if job else None
        qa_ready = bool(job and job.get("qa_ready"))
        qa_error = job.get("qa_error") if job else None

    if job is not None and status != "done":
        return _error("Q&A is available once the analysis finishes.", 409)
    if job is not None and not qa_ready:
        return _error(qa_error or "This analysis was not indexed for Q&A.", 409)

    try:
        if job is None and not has_index(job_id):
            return _error("Unknown job id.", 404)
        result = answer_question(job_id, question, history)
    except Exception as error:
        traceback.print_exc()
        return _error(str(error) or error.__class__.__name__, 502)

    return jsonify(result)


# ── History ──────────────────────────────────────────────────────────
@app.get("/api/analyses")
def list_analyses():
    return jsonify({"analyses": db.list_analyses()})


@app.get("/api/analyses/<analysis_id>")
def get_analysis(analysis_id: str):
    job = _stored_job(analysis_id)
    return jsonify(job) if job else _error("Unknown analysis id.", 404)


# ── Workspaces and the agent ─────────────────────────────────────────
def _workspace_or_404(workspace_id: str):
    workspace = db.get_workspace(workspace_id)
    return workspace, (None if workspace else _error("Unknown workspace id.", 404))


@app.get("/api/workspaces")
def list_workspaces():
    return jsonify({"workspaces": db.list_workspaces()})


@app.post("/api/workspaces")
def create_workspace():
    data = request.get_json(silent=True) or {}
    ids = [str(i) for i in data.get("analysis_ids") or [] if str(i).strip()]
    name = str(data.get("name") or "").strip()[:120]

    if not ids:
        return _error("Add at least one analyzed video.", 400)
    missing = [i for i in ids if db.get_analysis(i) is None]
    if missing:
        return _error("Some videos have not finished analyzing or no longer exist.", 400)

    if not name:
        titles = [db.get_analysis(i)["title"] or "Untitled" for i in ids[:2]]
        name = " vs ".join(titles) if len(ids) > 1 else titles[0]
    return jsonify(db.create_workspace(uuid.uuid4().hex, name[:120], list(dict.fromkeys(ids)))), 201


@app.get("/api/workspaces/<workspace_id>")
def get_workspace(workspace_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    return error or jsonify(workspace)


@app.delete("/api/workspaces/<workspace_id>")
def delete_workspace(workspace_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    if error:
        return error
    db.delete_workspace(workspace_id)
    return jsonify({"deleted": True})


@app.post("/api/workspaces/<workspace_id>/videos")
def add_workspace_video(workspace_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    if error:
        return error
    analysis_id = str((request.get_json(silent=True) or {}).get("analysis_id") or "")
    if db.get_analysis(analysis_id) is None:
        return _error("Unknown analysis id.", 400)
    db.add_workspace_video(workspace_id, analysis_id)
    return jsonify(db.get_workspace(workspace_id))


@app.delete("/api/workspaces/<workspace_id>/videos/<analysis_id>")
def remove_workspace_video(workspace_id: str, analysis_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    if error:
        return error
    db.remove_workspace_video(workspace_id, analysis_id)
    return jsonify(db.get_workspace(workspace_id))


@app.post("/api/workspaces/<workspace_id>/compare")
def compare_workspace(workspace_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    if error:
        return error
    videos = [db.get_analysis(v["id"]) for v in workspace["videos"]]
    if len(videos) < 2:
        return _error("Add at least two videos to compare them.", 400)
    try:
        comparison = compare_videos(videos)
    except Exception as error:
        traceback.print_exc()
        return _error(str(error) or error.__class__.__name__, 502)
    db.set_comparison(workspace_id, comparison)
    return jsonify({"comparison": comparison})


@app.post("/api/workspaces/<workspace_id>/chat")
def workspace_chat(workspace_id: str):
    workspace, error = _workspace_or_404(workspace_id)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []
    allow_web = data.get("allow_web", True) is not False

    if not message:
        return _error("A message is required.", 400)
    if len(message) > MAX_QUESTION_CHARS:
        return _error(f"Messages are limited to {MAX_QUESTION_CHARS} characters.", 400)
    if not workspace["videos"]:
        return _error("This workspace has no videos yet.", 409)

    try:
        result = run_agent(workspace["videos"], message, history, allow_web=allow_web)
    except Exception as error:
        traceback.print_exc()
        return _error(str(error) or error.__class__.__name__, 502)
    return jsonify(result)


# ── Contacts ─────────────────────────────────────────────────────────
def _contact_fields(data: dict, partial: bool = False):
    fields = {}
    for key in ("name", "email", "aliases"):
        if key in data:
            fields[key] = str(data.get(key) or "").strip()[:200]
    if not partial or "name" in fields:
        if not fields.get("name"):
            return None, "A name is required."
    if not partial or "email" in fields:
        if not EMAIL_RE.match(fields.get("email", "")):
            return None, "A valid email address is required."
    return fields, None


@app.get("/api/contacts")
def list_contacts():
    return jsonify({"contacts": db.list_contacts()})


@app.post("/api/contacts")
def create_contact():
    fields, problem = _contact_fields(request.get_json(silent=True) or {})
    if problem:
        return _error(problem, 400)
    try:
        return jsonify(db.create_contact(**fields)), 201
    except sqlite3.IntegrityError:
        return _error("A contact with that email already exists.", 409)


@app.put("/api/contacts/<int:contact_id>")
def update_contact(contact_id: int):
    if db.get_contact(contact_id) is None:
        return _error("Unknown contact id.", 404)
    fields, problem = _contact_fields(request.get_json(silent=True) or {}, partial=True)
    if problem:
        return _error(problem, 400)
    try:
        return jsonify(db.update_contact(contact_id, **fields))
    except sqlite3.IntegrityError:
        return _error("A contact with that email already exists.", 409)


@app.delete("/api/contacts/<int:contact_id>")
def delete_contact(contact_id: int):
    if not db.delete_contact(contact_id):
        return _error("Unknown contact id.", 404)
    return jsonify({"deleted": True})


# ── Meeting tasks ────────────────────────────────────────────────────
@app.get("/api/analyses/<analysis_id>/tasks")
def list_tasks(analysis_id: str):
    if db.get_analysis(analysis_id) is None:
        return _error("Unknown analysis id.", 404)
    return jsonify({"tasks": db.list_tasks(analysis_id)})


@app.post("/api/analyses/<analysis_id>/tasks/rematch")
def rematch_tasks(analysis_id: str):
    """Re-run owner matching, e.g. after adding contacts. Keeps manual picks."""
    if db.get_analysis(analysis_id) is None:
        return _error("Unknown analysis id.", 404)
    contacts = db.list_contacts()
    for task in db.list_tasks(analysis_id):
        if task["contact_id"]:
            continue
        matched = match_tasks([task], contacts)[0]
        if matched["contact_id"]:
            db.update_task(task["id"], contact_id=matched["contact_id"])
    return jsonify({"tasks": db.list_tasks(analysis_id)})


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id: int):
    task = db.get_task(task_id)
    if task is None:
        return _error("Unknown task id.", 404)

    data = request.get_json(silent=True) or {}
    fields = {}
    for key in ("text", "due", "owner_name"):
        if key in data:
            fields[key] = str(data.get(key) or "").strip()[:500]
    if "text" in fields and not fields["text"]:
        return _error("A task needs some text.", 400)

    if "contact_id" in data:
        contact_id = data.get("contact_id")
        if contact_id in (None, ""):
            fields["contact_id"] = None
        else:
            contact = db.get_contact(int(contact_id)) if str(contact_id).isdigit() else None
            if contact is None:
                return _error("Unknown contact id.", 400)
            fields["contact_id"] = contact["id"]
            fields.setdefault("owner_name", contact["name"])

    if "status" in data:
        if data["status"] not in ("proposed", "approved", "dismissed", "done"):
            return _error("Status must be proposed, approved, dismissed, or done.", 400)
        fields["status"] = data["status"]

    return jsonify(db.update_task(task_id, **fields))


# ── Emails ───────────────────────────────────────────────────────────
@app.post("/api/analyses/<analysis_id>/emails/draft")
def create_drafts(analysis_id: str):
    data = request.get_json(silent=True) or {}
    contact_ids = [int(i) for i in data.get("contact_ids") or [] if str(i).isdigit()] or None
    try:
        return jsonify(draft_emails(analysis_id, contact_ids))
    except LookupError as error:
        return _error(str(error), 404)


@app.get("/api/emails")
def list_emails():
    analysis_id = request.args.get("analysis", "")
    if not analysis_id:
        return _error("Pass ?analysis=<id>.", 400)
    return jsonify({"emails": db.list_emails(analysis_id)})


@app.patch("/api/emails/<int:email_id>")
def update_email(email_id: int):
    email = db.get_email(email_id)
    if email is None:
        return _error("Unknown email id.", 404)
    if email["status"] not in ("drafted", "failed", "dry-run"):
        return _error("Only unsent drafts can be edited.", 409)

    data = request.get_json(silent=True) or {}
    fields = {k: str(data[k]).strip() for k in ("subject", "body") if k in data}
    if any(not value for value in fields.values()):
        return _error("Subject and body cannot be empty.", 400)
    return jsonify(db.update_email(email_id, **{k: v[:20000] for k, v in fields.items()}))


def _within_send_limit() -> bool:
    with _send_lock:
        now = time.time()
        while _send_times and now - _send_times[0] > 60:
            _send_times.popleft()
        if len(_send_times) >= SEND_LIMIT_PER_MINUTE:
            return False
        _send_times.append(now)
        return True


@app.post("/api/emails/<int:email_id>/send")
def send_email(email_id: int):
    """Send one reviewed draft. This is the only code path that sends email."""
    email = db.get_email(email_id)
    if email is None:
        return _error("Unknown email id.", 404)
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return _error("Sending needs explicit confirmation.", 400)
    if email["status"] not in ("drafted", "failed", "dry-run"):
        return _error(f"This email was already {email['status']}.", 409)

    # The recipient must still be a saved contact with that exact address.
    contact = db.get_contact(email["contact_id"]) if email["contact_id"] else None
    if contact is None or contact["email"].lower() != email["to_addr"].lower():
        return _error("The recipient is not a saved contact. Re-draft this email.", 409)
    if not _within_send_limit():
        return _error("Too many emails sent in the last minute. Try again shortly.", 429)

    try:
        outcome = mailer.send(email["to_addr"], email["subject"], email["body"])
    except Exception as error:
        db.update_email(email_id, status="failed", error=str(error))
        db.set_task_status(email["task_ids"], "failed")
        return _error(str(error), 502)

    status = "sent" if outcome == "sent" else "dry-run"
    updated = db.update_email(email_id, status=status, error=None, sent_at=db.now())
    if status == "sent":
        db.set_task_status(email["task_ids"], "emailed")
    return jsonify(updated)


@app.get("/api/health")
def health():
    with JOBS_LOCK:
        running = sum(1 for job in JOBS.values() if job["status"] == "running")
        total = len(JOBS)

    return jsonify({
        "status": "ok",
        "whisper_model": WHISPER_MODEL,
        "gemini_key_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "web_search_enabled": os.getenv("WEB_SEARCH_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
        "smtp_configured": mailer.configured(),
        "email_dry_run": mailer.dry_run(),
        "jobs": {"running": running, "retained": total},
    })


if __name__ == "__main__":
    # threaded=True so status polling is answered while a job is running.
    app.run(debug=True, port=5000, threaded=True, use_reloader=False)
