"""Flask backend for the AI Video Assistant.

Analysis runs in a background thread because transcription can take several
minutes; the browser starts a job, then polls it for progress and results.
"""

import os
import threading
import traceback
import uuid
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

# Loaded here explicitly rather than relying on an import side effect elsewhere.
load_dotenv()

from utils.audio_processor import process_input
from core.transcriber import transcribe_all, Cancelled
from core.summarize import summarize_transcript
from core.extractor import extract_information

app = Flask(__name__)

# job_id -> job dict. In-memory only; fine for a single-process dev server.
JOBS = {}
JOBS_LOCK = threading.Lock()

# Finished jobs are dropped after this long to keep memory bounded.
JOB_TTL = timedelta(hours=2)
MAX_JOBS = 50

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# Percentage each stage has completed by the time it finishes.
STAGE_WEIGHTS = {"download": 15, "transcribe": 70, "summarize": 90, "extract": 100}


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


def _run_analysis(job_id: str, source: str) -> None:
    def step(stage: str, message: str, percent: int) -> None:
        print(f"[{job_id[:8]}] {message}")
        _update(job_id, stage=stage, message=message, percent=percent)

    def checkpoint() -> None:
        if _is_cancelled(job_id):
            raise Cancelled("Cancelled by the user.")

    try:
        checkpoint()
        step("download", "Downloading and preparing audio...", 5)
        chunks = process_input(
            source,
            on_metadata=lambda meta: _update(job_id, metadata=meta),
        )

        checkpoint()
        step(
            "transcribe",
            f"Transcribing {len(chunks)} audio chunk(s) with Whisper...",
            STAGE_WEIGHTS["download"],
        )

        def transcribe_progress(message: str, done: int, total: int) -> None:
            span = STAGE_WEIGHTS["transcribe"] - STAGE_WEIGHTS["download"]
            percent = STAGE_WEIGHTS["download"] + int(span * done / max(total, 1))
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
        step("summarize", "Writing the detailed summary...", STAGE_WEIGHTS["transcribe"])
        summary = summarize_transcript(
            transcript,
            progress=lambda msg: _update(job_id, message=msg),
            should_cancel=lambda: _is_cancelled(job_id),
        )
        _update(job_id, summary=summary)

        checkpoint()
        step(
            "extract",
            "Extracting action items, decisions, and questions...",
            STAGE_WEIGHTS["summarize"],
        )
        information = extract_information(summary)

        _update(
            job_id,
            status="done",
            stage="done",
            message="Analysis complete.",
            percent=100,
            action_items=information.get("action_items", ""),
            decisions=information.get("decisions", ""),
            questions=information.get("questions", ""),
            finished_at=datetime.now().isoformat(timespec="seconds"),
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


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    source = (data.get("url") or "").strip()

    if not source:
        return jsonify({"error": "A video URL or local file path is required."}), 400

    _prune_jobs()
    job_id = uuid.uuid4().hex

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "source": source,
            "status": "running",
            "stage": "queued",
            "message": "Queued...",
            "percent": 0,
            "metadata": None,
            "transcript": "",
            "summary": "",
            "action_items": "",
            "decisions": "",
            "questions": "",
            "error": None,
            "cancel_requested": False,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
        }

    threading.Thread(
        target=_run_analysis, args=(job_id, source), daemon=True
    ).start()

    return jsonify({"job_id": job_id}), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        snapshot = dict(job) if job else None

    if snapshot is None:
        return jsonify({"error": "Unknown job id."}), 404

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
            return jsonify({"error": "Unknown job id."}), 404
        if job["status"] != "running":
            return jsonify({"error": f"Job is already {job['status']}."}), 409
        job["cancel_requested"] = True
        job["message"] = "Cancelling after the current step..."

    return jsonify({"status": "cancelling"}), 202


@app.get("/api/health")
def health():
    with JOBS_LOCK:
        running = sum(1 for job in JOBS.values() if job["status"] == "running")
        total = len(JOBS)

    return jsonify({
        "status": "ok",
        "whisper_model": WHISPER_MODEL,
        "gemini_key_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "jobs": {"running": running, "retained": total},
    })


if __name__ == "__main__":
    # threaded=True so status polling is answered while a job is running.
    app.run(debug=True, port=5000, threaded=True, use_reloader=False)
