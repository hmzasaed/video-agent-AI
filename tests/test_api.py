"""Backend route and job-lifecycle tests.

The pipeline (yt-dlp, Whisper, Gemini, Chroma) is stubbed, so this suite runs in
seconds, needs no API key, and downloads nothing. It covers the routes, the
job state machine, progress reporting, cancellation, error handling, and Q&A.

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


# In-memory stand-in for the Chroma index: doc_id -> sections dict.
INDEX = {}
ASKED = []


def fake_index_video(doc_id, sections, title=""):
    INDEX[doc_id] = sections
    return sum(1 for text in sections.values() if text)


def fake_answer_question(doc_id, question, history=None):
    ASKED.append({"doc_id": doc_id, "question": question, "history": history})
    return {
        "answer": "They decided to ship on Friday [1].",
        "sources": [{"id": 1, "section": "transcript",
                     "text": INDEX[doc_id]["transcript"], "score": 0.8}],
    }


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

    vector_store = types.ModuleType("core.vector_store")
    vector_store.index_video = fake_index_video
    vector_store.has_index = lambda doc_id: doc_id in INDEX
    sys.modules["core.vector_store"] = vector_store

    rag_engine = types.ModuleType("core.rag_engine")
    rag_engine.answer_question = fake_answer_question
    sys.modules["core.rag_engine"] = rag_engine


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
    "panel-ask", "chatLog", "askForm", "askInput", "askBtn", "askNotice", "step-index",
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

# ── Q&A ─────────────────────────────────────────────────────────────
done_id = job["id"]
check("job is qa_ready", job["qa_ready"] is True and job["qa_error"] is None)
check("all sections indexed",
      set(INDEX.get(done_id, {})) == {"transcript", "summary", "action_items",
                                      "decisions", "questions"})

ask = client.post(f"/api/jobs/{done_id}/ask", json={
    "question": "  When do we ship?  ",
    "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
})
answer = ask.get_json()
check("POST /ask -> 200", ask.status_code == 200, str(answer))
check("answer returned", "Friday" in answer.get("answer", ""))
check("sources returned", answer.get("sources", [{}])[0].get("id") == 1)
check("question trimmed", ASKED[-1]["question"] == "When do we ship?")
check("history forwarded", len(ASKED[-1]["history"]) == 2)

check("blank question -> 400",
      client.post(f"/api/jobs/{done_id}/ask", json={"question": " "}).status_code == 400)
check("overlong question -> 400",
      client.post(f"/api/jobs/{done_id}/ask", json={"question": "x" * 1001}).status_code == 400)
check("ask unknown job -> 404",
      client.post("/api/jobs/nope/ask", json={"question": "hi"}).status_code == 404)

with flask_app.JOBS_LOCK:
    pruned = flask_app.JOBS.pop(done_id)
check("ask pruned job with persisted index -> 200",
      client.post(f"/api/jobs/{done_id}/ask", json={"question": "hi"}).status_code == 200)
with flask_app.JOBS_LOCK:
    flask_app.JOBS[done_id] = pruned

original_answer = flask_app.answer_question
flask_app.answer_question = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("Gemini API rate limit reached."))
failed_ask = client.post(f"/api/jobs/{done_id}/ask", json={"question": "hi"})
check("LLM failure -> 502 with message",
      failed_ask.status_code == 502 and "rate limit" in failed_ask.get_json()["error"])
flask_app.answer_question = original_answer

original_index = flask_app.index_video


def broken_index(*args, **kwargs):
    raise RuntimeError("embedding model download failed")


flask_app.index_video = broken_index
job = wait_for(start("https://youtu.be/noindex"))
check("indexing failure still finishes job", job["status"] == "done", job.get("error") or "")
check("indexing failure reported",
      job["qa_ready"] is False and "embedding" in (job["qa_error"] or ""))
not_ready = client.post(f"/api/jobs/{job['id']}/ask", json={"question": "hi"})
check("ask unindexed job -> 409", not_ready.status_code == 409)
flask_app.index_video = original_index

# ── Cancellation ────────────────────────────────────────────────────
slow_id = start("https://youtu.be/slow")
time.sleep(0.2)
running_ask = client.post(f"/api/jobs/{slow_id}/ask", json={"question": "hi"})
check("ask running job -> 409", running_ask.status_code == 409)
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
