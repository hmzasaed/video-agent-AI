"""Backend route and job-lifecycle tests.

The pipeline (yt-dlp, Whisper, Gemini) is stubbed, so this suite runs in
seconds, needs no API key, and downloads nothing. It covers the routes, the
job state machine, progress reporting, cancellation, and error handling.

Run:  python tests/test_api.py
"""

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

FAKE_META = {
    "title": "Q3 Platform Sync",
    "uploader": "Engineering",
    "duration": 1845,
    "thumbnail": "https://example.com/t.jpg",
    "webpage_url": "https://youtu.be/demo",
}


class Cancelled(Exception):
    pass


# ── Stubs, installed before app.py imports these names ──────────────
def fake_process(src, on_metadata=None):
    if on_metadata:
        on_metadata(FAKE_META)
    return ["a.wav", "b.wav"]


def fake_transcribe(chunks, model_name="base", progress=None, should_cancel=None):
    total = len(chunks)
    for index in range(total):
        if should_cancel and should_cancel():
            raise Cancelled("cancelled")
        if progress:
            progress(f"Transcribing chunk {index + 1}/{total}...", index, total)
        time.sleep(0.15)
    return "We decided to ship on Friday. Who owns QA? Sarah rewrites the refund path."


def fake_summarize(transcript, progress=None, should_cancel=None):
    if progress:
        progress("Summarizing section 1/1...")
    return "📌 **Ship Meeting**\n\n## Overview\n- Ship on Friday"


def install_stubs():
    audio = types.ModuleType("utils.audio_processor")
    audio.process_input = fake_process
    sys.modules["utils.audio_processor"] = audio

    transcriber = types.ModuleType("core.transcriber")
    transcriber.transcribe_all = fake_transcribe
    transcriber.Cancelled = Cancelled
    sys.modules["core.transcriber"] = transcriber

    summarize = types.ModuleType("core.summarize")
    summarize.summarize_transcript = fake_summarize
    summarize.Cancelled = Cancelled
    sys.modules["core.summarize"] = summarize

    extractor = types.ModuleType("core.extractor")
    extractor.extract_information = lambda text: {
        "action_items": "- Assign QA owner",
        "decisions": "- Ship on Friday",
        "questions": "- Who owns QA?",
    }
    sys.modules["core.extractor"] = extractor


install_stubs()

import app as flask_app  # noqa: E402  (must follow install_stubs)

client = flask_app.app.test_client()
passed, failed = [], []


def check(label, ok, detail=""):
    (passed if ok else failed).append(label)
    print(f"{'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")


def wait_for(job_id, timeout=15):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    return job


def start(url):
    return client.post("/api/analyze", json={"url": url}).get_json()["job_id"]


# ── Health and page ─────────────────────────────────────────────────
health = client.get("/api/health")
payload = health.get_json()
check("GET /api/health", health.status_code == 200 and payload["status"] == "ok")
check("health reports whisper model", "whisper_model" in payload)
check("health reports job counts", "jobs" in payload)

page = client.get("/")
html = page.get_data(as_text=True)
check("GET / renders", page.status_code == 200 and "AI Video Assistant" in html)
check("template resolved", "url_for" not in html and "/static/" in html)

for element in [
    "cancelBtn", "videoMeta", "transcriptSearch", "panelStats", "downloadMdBtn",
    "downloadTxtBtn", "recentList", "toast", "percentLabel", "transcriptText",
    "metaThumb", "clearRecent", "progressFill", "errorMessage",
]:
    check(f"page has #{element}", f'id="{element}"' in html)

check("tabs use ARIA", 'role="tablist"' in html and 'aria-selected' in html)
check("progress bar is labelled", 'role="progressbar"' in html)

# ── Validation ──────────────────────────────────────────────────────
blank = client.post("/api/analyze", json={"url": "   "})
check("blank url -> 400", blank.status_code == 400)
check("no body -> 400", client.post("/api/analyze").status_code == 400)
check("unknown job -> 404", client.get("/api/jobs/nope").status_code == 404)
check("cancel unknown job -> 404", client.post("/api/jobs/nope/cancel").status_code == 404)

# ── Happy path ──────────────────────────────────────────────────────
response = client.post("/api/analyze", json={"url": "https://youtu.be/demo"})
check("POST /api/analyze -> 202", response.status_code == 202)

job = wait_for(response.get_json()["job_id"])
check("job completes", job["status"] == "done", job.get("error") or "")
check("percent reaches 100", job["percent"] == 100)
check("stage is done", job["stage"] == "done")
check("metadata captured", bool(job["metadata"]) and job["metadata"]["title"] == FAKE_META["title"])
check("summary present", "Ship Meeting" in job["summary"])
check("action items present", bool(job["action_items"]))
check("decisions present", bool(job["decisions"]))
check("questions present", bool(job["questions"]))
check("transcript present", bool(job["transcript"]))
check("timestamps set", bool(job["started_at"]) and bool(job["finished_at"]))

conflict = client.post(f"/api/jobs/{job['id']}/cancel")
check("cancel finished job -> 409", conflict.status_code == 409)

# ── Cancellation ────────────────────────────────────────────────────
slow_id = start("https://youtu.be/slow")
time.sleep(0.2)
cancel = client.post(f"/api/jobs/{slow_id}/cancel")
check("cancel running job -> 202", cancel.status_code == 202)
check("job reports cancelled", wait_for(slow_id)["status"] == "cancelled")

# ── Failure paths ───────────────────────────────────────────────────
original_process = flask_app.process_input


def boom(src, on_metadata=None):
    raise RuntimeError("yt-dlp could not reach that URL")


flask_app.process_input = boom
job = wait_for(start("https://youtu.be/broken"))
check("pipeline error propagates",
      job["status"] == "error" and "yt-dlp" in (job["error"] or ""),
      job.get("error") or "")

flask_app.process_input = original_process
flask_app.transcribe_all = lambda *args, **kwargs: "   "
job = wait_for(start("https://youtu.be/silent"))
check("silent audio guarded",
      job["status"] == "error" and "No speech" in (job["error"] or ""),
      job.get("error") or "")

# ── Summary ─────────────────────────────────────────────────────────
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + ", ".join(failed))
    sys.exit(1)
