"""Backend route and job-lifecycle tests.

The pipeline (yt-dlp, Whisper, Gemini, Chroma) is stubbed, so this suite runs in
seconds, needs no API key, and downloads nothing. It covers the routes, the
job state machine, progress reporting, cancellation, error handling, and Q&A.

Run:  python tests/test_api.py
"""

import io
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

# Isolated database, and no real email ever leaves the test run.
TMP = tempfile.mkdtemp(prefix="ava-test-")
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["EMAIL_DRY_RUN"] = "true"

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

    meeting = types.ModuleType("core.meeting")
    meeting.meeting_minutes = lambda transcript: "## Attendees mentioned\n- Sarah Khan\n- Omar"
    meeting.extract_tasks = fake_extract_tasks
    real_meeting = _load_real("core/meeting.py", "real_meeting")
    meeting.match_tasks = real_meeting.match_tasks
    meeting.match_owner = real_meeting.match_owner
    sys.modules["core.meeting"] = meeting

    agent = types.ModuleType("core.agent")
    agent.run_agent = fake_run_agent
    agent.compare_videos = lambda videos: f"## Common ground\n{len(videos)} videos compared"
    sys.modules["core.agent"] = agent


def _load_real(relative, name):
    """Load a module from source without importing its Gemini dependencies."""
    import importlib.util
    gemini = types.ModuleType("core.gemini_client")
    gemini.generate_text = gemini.generate_json = lambda *a, **k: ""
    saved = sys.modules.get("core.gemini_client")
    sys.modules["core.gemini_client"] = gemini
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is not None:
            sys.modules["core.gemini_client"] = saved
        else:
            del sys.modules["core.gemini_client"]


def fake_extract_tasks(transcript, minutes=""):
    return {
        "people": ["Sarah", "Omar", "Priya"],
        "tasks": [
            {"text": "Rewrite the refund path", "owner_name": "Sarah", "due": "Friday",
             "evidence": "Sarah rewrites the refund path."},
            {"text": "Own QA sign-off", "owner_name": "Omar", "due": "", "evidence": "Who owns QA?"},
            {"text": "Update the release notes", "owner_name": "Priya", "due": "", "evidence": ""},
        ],
    }


AGENT_CALLS = []


def fake_run_agent(videos, message, history=None, allow_web=True):
    AGENT_CALLS.append({"videos": [v["id"] for v in videos], "message": message,
                        "allow_web": allow_web})
    return {"answer": "Both talks cover shipping [V1-1] [W1].",
            "sources": [{"id": "V1-1", "type": "video"}, {"id": "W1", "type": "web"}],
            "steps": [{"tool": "search_videos", "args": {}, "summary": "1 excerpt"}],
            "drafts": []}


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
    # meetings, uploads, workspaces, contacts, email
    "apptab-analyze", "apptab-compare", "apptab-meetings", "app-compare", "app-meetings",
    "modeVideo", "modeMeeting", "uploadBtn", "fileInput", "uploadChip", "inputCard",
    "step-tasks", "stepList", "panel-minutes", "panel-tasks", "addToWorkspaceBtn",
    "historyList", "wsList", "wsCreate", "wsView", "wsCompareBtn", "wsComparison",
    "wsChatLog", "wsChatForm", "wsInput", "wsWeb", "wsSuggestions",
    "meetingList", "meetView", "meetTasks", "contactForm", "contactList",
    "sendDialog", "sendDialogConfirm", "sendDialogList",
]:
    check(f"page has #{element}", f'id="{element}"' in html)

for script in ("main.js", "meetings.js", "agent.js", "site.js"):
    check(f"page loads {script}", f"js/{script}" in html)

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
      set(INDEX.get(done_id, {})) == {"transcript", "summary", "minutes", "action_items",
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

# ── History / persistence ───────────────────────────────────────────
flask_app.transcribe_all = fake_transcribe
listing = client.get("/api/analyses").get_json()["analyses"]
check("finished analysis persisted", any(a["id"] == done_id for a in listing))
stored = client.get(f"/api/analyses/{done_id}")
check("GET /api/analyses/<id> -> 200", stored.status_code == 200
      and "Ship Meeting" in stored.get_json()["summary"])
check("unknown analysis -> 404", client.get("/api/analyses/nope").status_code == 404)
with flask_app.JOBS_LOCK:
    pruned = flask_app.JOBS.pop(done_id)
restored = client.get(f"/api/jobs/{done_id}")
check("pruned job served from the database",
      restored.status_code == 200 and restored.get_json()["status"] == "done"
      and bool(restored.get_json()["transcript"]))
with flask_app.JOBS_LOCK:
    flask_app.JOBS[done_id] = pruned

# ── Uploads ─────────────────────────────────────────────────────────
check("upload without file -> 400", client.post("/api/upload").status_code == 400)
bad = client.post("/api/upload", data={"file": (io.BytesIO(b"x"), "notes.exe")},
                  content_type="multipart/form-data")
check("upload wrong type -> 400", bad.status_code == 400)
good = client.post("/api/upload", data={"file": (io.BytesIO(b"RIFF...."), "Weekly Sync.wav")},
                   content_type="multipart/form-data")
uploaded = good.get_json()
check("upload -> 201 with path", good.status_code == 201 and os.path.isfile(uploaded["path"]))
check("upload keeps the original name", uploaded.get("name") == "Weekly Sync")
check("upload stored under a random name", "Weekly" not in os.path.basename(uploaded["path"]))
os.remove(uploaded["path"])
limit = flask_app.app.config["MAX_CONTENT_LENGTH"]
flask_app.app.config["MAX_CONTENT_LENGTH"] = 10
huge = client.post("/api/upload", data={"file": (io.BytesIO(b"x" * 100), "big.mp4")},
                   content_type="multipart/form-data")
check("oversized upload -> 413", huge.status_code == 413)
flask_app.app.config["MAX_CONTENT_LENGTH"] = limit


# ── Contacts ────────────────────────────────────────────────────────
def add_contact(name, email, aliases=""):
    return client.post("/api/contacts", json={"name": name, "email": email, "aliases": aliases})


sarah = add_contact("Sarah Khan", "sarah@example.com")
check("create contact -> 201", sarah.status_code == 201)
sarah = sarah.get_json()
omar = add_contact("Omar Ali", "omar@example.com", "O").get_json()
check("invalid email -> 400", add_contact("X", "not-an-email").status_code == 400)
check("missing name -> 400", add_contact("", "a@b.co").status_code == 400)
check("duplicate email -> 409 (case-insensitive)",
      add_contact("Sarah 2", "SARAH@example.com").status_code == 409)
renamed = client.put(f"/api/contacts/{omar['id']}", json={"aliases": "O, OA"})
check("update contact -> 200", renamed.status_code == 200 and renamed.get_json()["aliases"] == "O, OA")
check("update unknown contact -> 404", client.put("/api/contacts/9999", json={}).status_code == 404)
check("list contacts", len(client.get("/api/contacts").get_json()["contacts"]) == 2)

# ── Meeting analysis ────────────────────────────────────────────────
meeting_start = client.post("/api/analyze", json={
    "url": "C:/meetings/sync.mp4", "kind": "meeting", "title": "Weekly sync"})
meeting = wait_for(meeting_start.get_json()["job_id"])
meeting_id = meeting["id"]
check("meeting job completes", meeting["status"] == "done", meeting.get("error") or "")
check("meeting kind recorded", meeting["kind"] == "meeting")
check("meeting title override", meeting["metadata"]["title"] == "Weekly sync")
check("minutes written", "Attendees" in meeting["minutes"])
check("minutes indexed", bool(INDEX.get(meeting_id, {}).get("minutes")))
tasks = meeting["tasks"]
check("three tasks extracted", len(tasks) == 3)
by_owner = {t["owner_name"]: t for t in tasks}
check("first name matched to contact", by_owner["Sarah"]["contact_id"] == sarah["id"])
check("second owner matched", by_owner["Omar"]["contact_id"] == omar["id"])
check("unknown person left unmatched", by_owner["Priya"]["contact_id"] is None)
check("video jobs have no tasks", client.get(f"/api/jobs/{done_id}").get_json()["tasks"] == [])

listed = client.get(f"/api/analyses/{meeting_id}/tasks").get_json()["tasks"]
check("GET tasks", len(listed) == 3 and listed[0]["contact_email"] == "sarah@example.com")

priya = add_contact("Priya Nair", "priya@example.com").get_json()
rematched = client.post(f"/api/analyses/{meeting_id}/tasks/rematch").get_json()["tasks"]
check("rematch picks up new contacts",
      next(t for t in rematched if t["owner_name"] == "Priya")["contact_id"] == priya["id"])

task_priya = next(t for t in rematched if t["owner_name"] == "Priya")
task_omar = next(t for t in rematched if t["owner_name"] == "Omar")
check("empty task text -> 400",
      client.patch(f"/api/tasks/{task_priya['id']}", json={"text": " "}).status_code == 400)
check("unknown contact -> 400",
      client.patch(f"/api/tasks/{task_priya['id']}", json={"contact_id": 999}).status_code == 400)
check("invalid status -> 400",
      client.patch(f"/api/tasks/{task_priya['id']}", json={"status": "emailed"}).status_code == 400)
reassigned = client.patch(f"/api/tasks/{task_priya['id']}", json={"contact_id": omar["id"]}).get_json()
check("@mention reassigns owner", reassigned["contact_id"] == omar["id"]
      and reassigned["owner_name"] == "Omar Ali")
dismissed = client.patch(f"/api/tasks/{task_omar['id']}", json={"status": "dismissed"}).get_json()
check("task dismissed", dismissed["status"] == "dismissed")
check("patch unknown task -> 404", client.patch("/api/tasks/9999", json={}).status_code == 404)

# ── Email drafts and sending ────────────────────────────────────────
drafted = client.post(f"/api/analyses/{meeting_id}/emails/draft", json={}).get_json()
drafts = {d["to_addr"]: d for d in drafted["drafts"]}
check("one draft per person", set(drafts) == {"sarah@example.com", "omar@example.com"})
check("dismissed task left out", "Own QA sign-off" not in drafts["omar@example.com"]["body"])
check("reassigned task included", "release notes" in drafts["omar@example.com"]["body"])
check("draft lists due date and evidence",
      "Due: Friday" in drafts["sarah@example.com"]["body"]
      and "refund path" in drafts["sarah@example.com"]["body"])
check("draft subject names the meeting", "Weekly sync" in drafts["sarah@example.com"]["subject"])
redraft = client.post(f"/api/analyses/{meeting_id}/emails/draft", json={}).get_json()
all_emails = client.get(f"/api/emails?analysis={meeting_id}").get_json()["emails"]
check("re-drafting replaces unsent drafts", len(all_emails) == 2)
drafts = {d["to_addr"]: d for d in redraft["drafts"]}
check("draft unknown analysis -> 404",
      client.post("/api/analyses/nope/emails/draft", json={}).status_code == 404)
check("emails list needs analysis -> 400", client.get("/api/emails").status_code == 400)

sarah_mail = drafts["sarah@example.com"]
edited = client.patch(f"/api/emails/{sarah_mail['id']}", json={"subject": "Your tasks"})
check("edit draft subject", edited.status_code == 200 and edited.get_json()["subject"] == "Your tasks")
check("empty body -> 400",
      client.patch(f"/api/emails/{sarah_mail['id']}", json={"body": ""}).status_code == 400)

check("send without confirmation -> 400",
      client.post(f"/api/emails/{sarah_mail['id']}/send", json={}).status_code == 400)
dry = client.post(f"/api/emails/{sarah_mail['id']}/send", json={"confirm": True}).get_json()
check("dry-run send logs without sending", dry["status"] == "dry-run")
check("dry-run leaves tasks unsent", all(
    t["status"] == "drafted" for t in client.get(f"/api/analyses/{meeting_id}/tasks").get_json()["tasks"]
    if t["contact_id"] == sarah["id"]))

import core.mailer as mailer_module  # noqa: E402

SENT = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        SENT.append(message)


real_smtp = mailer_module.smtplib.SMTP
mailer_module.smtplib.SMTP = FakeSMTP
os.environ.update({"EMAIL_DRY_RUN": "false", "SMTP_HOST": "smtp.example.com",
                   "SMTP_USER": "me@example.com", "SMTP_PASSWORD": "x", "SMTP_FROM": "me@example.com"})
sent = client.post(f"/api/emails/{sarah_mail['id']}/send", json={"confirm": True})
check("send -> 200 sent", sent.status_code == 200 and sent.get_json()["status"] == "sent")
check("SMTP received one message to the contact",
      len(SENT) == 1 and SENT[0]["To"] == "sarah@example.com" and SENT[0]["Subject"] == "Your tasks")
check("sent tasks marked emailed", all(
    t["status"] == "emailed" for t in client.get(f"/api/analyses/{meeting_id}/tasks").get_json()["tasks"]
    if t["contact_id"] == sarah["id"]))
check("sending twice -> 409",
      client.post(f"/api/emails/{sarah_mail['id']}/send", json={"confirm": True}).status_code == 409)

omar_mail = drafts["omar@example.com"]
client.put(f"/api/contacts/{omar['id']}", json={"email": "omar.new@example.com"})
stale = client.post(f"/api/emails/{omar_mail['id']}/send", json={"confirm": True})
check("recipient no longer a contact -> 409", stale.status_code == 409 and len(SENT) == 1)

mailer_module.smtplib.SMTP = real_smtp
os.environ["EMAIL_DRY_RUN"] = "true"
health = client.get("/api/health").get_json()
check("health reports email settings", health["email_dry_run"] is True and "smtp_configured" in health)

# ── Workspaces and agent ────────────────────────────────────────────
check("workspace needs videos -> 400",
      client.post("/api/workspaces", json={"analysis_ids": []}).status_code == 400)
check("workspace unknown video -> 400",
      client.post("/api/workspaces", json={"analysis_ids": ["nope"]}).status_code == 400)
created = client.post("/api/workspaces", json={"analysis_ids": [done_id, meeting_id]})
workspace = created.get_json()
check("create workspace -> 201", created.status_code == 201 and len(workspace["videos"]) == 2)
check("workspace auto-named", " vs " in workspace["name"])
ws_id = workspace["id"]
check("list workspaces",
      any(w["id"] == ws_id for w in client.get("/api/workspaces").get_json()["workspaces"]))

compared = client.post(f"/api/workspaces/{ws_id}/compare")
check("compare -> 200", compared.status_code == 200 and "2 videos" in compared.get_json()["comparison"])
check("comparison cached", "2 videos" in client.get(f"/api/workspaces/{ws_id}").get_json()["comparison"])

check("chat blank -> 400",
      client.post(f"/api/workspaces/{ws_id}/chat", json={"message": " "}).status_code == 400)
chat = client.post(f"/api/workspaces/{ws_id}/chat",
                   json={"message": "Compare them", "allow_web": False})
result = chat.get_json()
check("chat -> 200 with answer", chat.status_code == 200 and "[V1-1]" in result["answer"])
check("chat returns sources and steps", len(result["sources"]) == 2 and bool(result["steps"]))
check("agent sees workspace videos", AGENT_CALLS[-1]["videos"] == [done_id, meeting_id])
check("web toggle forwarded", AGENT_CALLS[-1]["allow_web"] is False)

removed = client.delete(f"/api/workspaces/{ws_id}/videos/{meeting_id}").get_json()
check("remove video", [v["id"] for v in removed["videos"]] == [done_id])
check("compare needs two videos -> 400",
      client.post(f"/api/workspaces/{ws_id}/compare").status_code == 400)
added = client.post(f"/api/workspaces/{ws_id}/videos", json={"analysis_id": meeting_id}).get_json()
check("add video back", len(added["videos"]) == 2)
check("unknown workspace -> 404", client.get("/api/workspaces/nope").status_code == 404)
check("delete workspace",
      client.delete(f"/api/workspaces/{ws_id}").status_code == 200
      and client.get(f"/api/workspaces/{ws_id}").status_code == 404)

# ── Summary ─────────────────────────────────────────────────────────
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + ", ".join(failed))
    sys.exit(1)
