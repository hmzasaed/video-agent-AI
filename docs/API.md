# API Reference

Base URL when running locally: `http://127.0.0.1:5000`

All request and response bodies are JSON, except `POST /api/upload`
(multipart). Errors always carry an `error` string.

**No authentication.** The server assumes it is reachable only from localhost.
See [SECURITY.md](SECURITY.md) before exposing it.

| Area | Endpoints |
| --- | --- |
| [Analysis jobs](#analysis-jobs) | `POST /api/upload` · `POST /api/analyze` · `GET /api/jobs/<id>` · `POST /api/jobs/<id>/cancel` · `POST /api/jobs/<id>/ask` |
| [History](#history) | `GET /api/analyses` · `GET /api/analyses/<id>` |
| [Workspaces & agent](#workspaces-and-the-research-agent) | `GET/POST /api/workspaces` · `GET/DELETE /api/workspaces/<id>` · `POST/DELETE …/videos` · `POST …/compare` · `POST …/chat` |
| [Contacts](#contacts) | `GET/POST /api/contacts` · `PUT/DELETE /api/contacts/<id>` |
| [Meeting tasks](#meeting-tasks) | `GET /api/analyses/<id>/tasks` · `POST …/tasks/rematch` · `PATCH /api/tasks/<id>` |
| [Emails](#emails) | `POST /api/analyses/<id>/emails/draft` · `GET /api/emails` · `PATCH /api/emails/<id>` · `POST /api/emails/<id>/send` |
| [Health](#get-apihealth) | `GET /api/health` |

---

## Analysis jobs

### Lifecycle

Analysis is asynchronous. One request starts it, later requests poll it.

```text
POST /api/upload   ──► 201 { path, name }          (optional: for a local recording)
POST /api/analyze  ──► 202 { job_id }
                          │
        ┌─────────────────┘
        ▼
GET /api/jobs/<id> ──► 200 { status: "running", percent, message, … }
        │  repeat every ~2s
        ▼
GET /api/jobs/<id> ──► 200 { status: "done", summary, transcript, tasks, qa_ready, … }
        │
        ▼
POST /api/jobs/<id>/ask ──► 200 { answer, sources }   (any number of times)
```

A finished analysis is written to SQLite, so `GET /api/jobs/<id>` keeps
working after the job leaves memory or the server restarts.

### `POST /api/upload`

Upload an audio or video recording (multipart form field `file`). Returns a
server path to pass to `/api/analyze`.

| Code | Body | When |
| --- | --- | --- |
| `201` | `{"path": "C:/…/downloads/uploads/3f9c….mp4", "name": "Weekly sync"}` | Saved |
| `400` | `{"error": "Unsupported file type. Use one of: …"}` | No file, or extension not in mp4, mkv, mov, webm, mp3, m4a, wav, ogg |
| `413` | `{"error": "That file is larger than the 2048 MB limit."}` | Over `MAX_UPLOAD_MB` |

Files are stored under random names; `name` is the original filename without
its extension, for use as the analysis title.

```bash
curl -F "file=@weekly-sync.mp4" http://127.0.0.1:5000/api/upload
```

### `POST /api/analyze`

Start an analysis.

```json
{ "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "kind": "video" }
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `url` | string | yes | A `http(s)` URL, or a path to a local media file **on the server** (e.g. from `/api/upload`) |
| `kind` | string | no | `video` (default) or `meeting`. Meetings add minutes and a `tasks` stage |
| `title` | string | no | Display title; overrides the file name for local files |

`202 Accepted` → `{"job_id": "7c273e666ea44b33b6103d871f9f3946"}`
`400 Bad Request` → `{"error": "A video URL or local file path is required."}`

A `202` means the job *started*, not that the URL is valid — a bad URL
surfaces later as a job with `status: "error"`.

### `GET /api/jobs/<job_id>`

Fetch the current state of a job. Safe to poll; 2 seconds is the interval the
bundled frontend uses. Falls back to the stored analysis if the job is no
longer in memory.

```json
{
  "id": "761355ede63a4eeb885ad2ef5a650e3a",
  "kind": "meeting",
  "source": "C:/…/downloads/uploads/53075e27….wav",
  "status": "done",
  "stage": "done",
  "message": "Analysis complete.",
  "percent": 100,
  "metadata": { "title": "Weekly product sync", "uploader": "", "duration": 0, "thumbnail": "" },
  "transcript": "Okay, let's start the weekly product sync…",
  "summary": "📌 **Weekly Product Sync: Checkout Release Plan**\n\n…",
  "minutes": "## Attendees mentioned\n- Sarah\n- Omar\n- Priya\n…",
  "action_items": "…",
  "decisions": "…",
  "questions": "…",
  "people": ["Sarah", "Omar", "Priya"],
  "tasks": [
    { "id": 1, "text": "finish rewriting the refund flow", "owner_name": "Sarah",
      "contact_id": 1, "contact_name": "Sarah Khan", "contact_email": "sarah.khan@example.com",
      "due": "Wednesday", "evidence": "Sarah, can you finish rewriting the refund flow by Wednesday?",
      "status": "proposed" }
  ],
  "qa_ready": true,
  "qa_error": null,
  "error": null,
  "started_at": "2026-09-29T17:14:10",
  "finished_at": "2026-09-29T17:15:14"
}
```

`404 Not Found` → `{"error": "Unknown job id."}`

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | string | `video` · `meeting` |
| `status` | string | `running` · `done` · `error` · `cancelled` |
| `stage` | string | `queued` · `download` · `transcribe` · `summarize` · `extract` · `tasks` (meetings) · `index` · `done` · `error` · `cancelled` |
| `message` | string | Human-readable current step, e.g. `Transcribing chunk 2/5...` |
| `percent` | int | 0–100, weighted by real time per stage |
| `metadata` | object \| null | Populated during `download` |
| `transcript`, `summary` | string | Markdown summary; fill in progressively |
| `minutes` | string | Meetings only: attendees, agenda, discussion, decisions, next steps |
| `action_items`, `decisions`, `questions` | string | Markdown, or an exact "none found" sentinel |
| `people` | array | Meetings only: names mentioned |
| `tasks` | array | Meetings only: structured tasks (see [Meeting tasks](#meeting-tasks)) |
| `qa_ready` / `qa_error` | bool / string | Whether the Q&A index was built, and why not |
| `error` | string \| null | Set only when `status` is `error` |

### `POST /api/jobs/<job_id>/cancel`

| Code | Body | When |
| --- | --- | --- |
| `202` | `{"status": "cancelling"}` | Accepted; job is running |
| `404` | `{"error": "Unknown job id."}` | No such job |
| `409` | `{"error": "Job is already done."}` | Already finished, failed, or cancelled |

Cancellation is cooperative — checked between stages and audio chunks.

### `POST /api/jobs/<job_id>/ask`

Ask a question about **one** finished analysis (RAG over its index, numbered
citations). For questions across several videos, use a
[workspace chat](#post-apiworkspacesidchat).

```json
{ "question": "Who owns the refund flow?", "history": [{ "role": "user", "content": "…" }] }
```

`200 OK`

```json
{
  "answer": "Sarah owns it and finishes by Wednesday [1].",
  "sources": [{ "id": 1, "section": "transcript", "text": "…", "score": 0.61 }]
}
```

`section` is one of `summary` · `minutes` · `action_items` · `decisions` ·
`questions` · `transcript`. Errors: `400` blank/overlong (max 1000 chars),
`404` unknown, `409` not finished or not indexed, `502` Gemini failure.

---

## History

### `GET /api/analyses`

Finished analyses, newest first, without the large text fields.

```json
{ "analyses": [
  { "id": "761355ed…", "kind": "meeting", "source": "…", "title": "Weekly product sync",
    "metadata": {…}, "qa_ready": true, "started_at": "…", "finished_at": "…" }
] }
```

### `GET /api/analyses/<id>`

One stored analysis in the same shape as a finished job (including `tasks` for
meetings). `404` if unknown.

---

## Workspaces and the research agent

A workspace groups analyzed videos and meetings. Inside a workspace they are
referred to as `V1`, `V2`, … in the order added.

### `POST /api/workspaces`

```json
{ "analysis_ids": ["2b08f3ce…", "bdc0f902…"], "name": "Release cadence research" }
```

`201` → the workspace (see below). `name` is optional ("Title A vs Title B"
by default). `400` if no ids, or an id is unknown or unfinished.

### `GET /api/workspaces` · `GET /api/workspaces/<id>` · `DELETE /api/workspaces/<id>`

```json
{
  "id": "e1c0…", "name": "Release cadence research", "created_at": "…",
  "comparison": "## At a glance\n- V1: …",
  "videos": [{ "id": "2b08f3ce…", "kind": "video", "title": "Ship every week",
               "metadata": {…}, "summary": "…", "qa_ready": true }]
}
```

The list endpoint returns `{"workspaces": [{id, name, created_at, video_count}]}`.

### `POST /api/workspaces/<id>/videos` · `DELETE /api/workspaces/<id>/videos/<analysis_id>`

Add `{"analysis_id": "…"}` or remove a video. Returns the updated workspace.
Changing the videos clears the cached comparison.

### `POST /api/workspaces/<id>/compare`

Generate (and cache) a side-by-side comparison from the stored summaries:
*At a glance · Common ground · Key differences · Unique to each · Which to
watch*. `200 {"comparison": "…markdown…"}`, `400` with fewer than two videos,
`502` on a Gemini failure.

### `POST /api/workspaces/<id>/chat`

Ask the research agent. It chooses tools — video search, summaries, web
search, meeting tasks, email drafts — and answers with citations.

```json
{
  "message": "Which talk does the meeting's plan fit better?",
  "history": [{ "role": "user", "content": "…" }, { "role": "assistant", "content": "…" }],
  "allow_web": true
}
```

`200 OK`

```json
{
  "answer": "It aligns more with V2's monthly approach [V2-1, V3-3]…",
  "sources": [
    { "id": "V3-3", "type": "video", "video": "V3", "title": "Weekly product sync",
      "section": "minutes", "text": "…" },
    { "id": "V1", "type": "video", "video": "V1", "title": "Ship every week",
      "section": "summary", "text": "…" },
    { "id": "W1", "type": "web", "title": "dora.dev", "url": "https://…" }
  ],
  "steps": [
    { "tool": "search_videos", "args": { "query": "release plan" }, "summary": "6 excerpt(s) for \"release plan\"" }
  ],
  "drafts": []
}
```

| Citation | Meaning |
| --- | --- |
| `[V1-3]` | Excerpt 3 retrieved from video 1 in this answer |
| `[V1]` | Video 1's stored summary or minutes |
| `[W2]` | Web source 2 (Google Search grounding) |

- `steps` is the tool trace shown as "How I answered".
- `drafts` lists email drafts the agent prepared (status `drafted`). The agent
  **cannot send email**; see [Emails](#emails).
- `allow_web: false` removes the web tool for that request. `WEB_SEARCH_ENABLED=false`
  disables it server-wide.
- The agent makes at most `AGENT_MAX_STEPS` tool calls (default 6), then answers
  with what it has.
- Errors: `400` blank/overlong message, `404` unknown workspace, `409` no
  videos, `502` Gemini failure.

**Agent tools**

| Tool | What it does |
| --- | --- |
| `list_videos` | Lists the workspace's videos and meetings |
| `get_summary(video)` | Reads a stored summary (or meeting minutes); citable as `[V1]` |
| `search_videos(query, videos?)` | Semantic search across the workspace's Chroma index |
| `web_search(query)` | Google Search grounding via a separate Gemini call |
| `list_tasks(video)` | A meeting's tasks and owners |
| `draft_task_emails(video, people?)` | Creates drafts for saved contacts only |

---

## Contacts

People that meeting owners are matched against, and the only allowed email
recipients.

| Method | Endpoint | Body | Result |
| --- | --- | --- | --- |
| `GET` | `/api/contacts` | — | `{"contacts": [{id, name, email, aliases}]}` |
| `POST` | `/api/contacts` | `{"name", "email", "aliases?"}` | `201` contact · `400` invalid · `409` duplicate email (case-insensitive) |
| `PUT` | `/api/contacts/<id>` | any of `name`, `email`, `aliases` | `200` contact · `404` · `409` |
| `DELETE` | `/api/contacts/<id>` | — | `{"deleted": true}` · `404` |

`aliases` is a comma-separated list of other names ("Sam, Sammy").

**Matching** (spoken name → contact): exact full name → alias → unique first
name → fuzzy match (≥ 0.85 similarity). Ambiguous first names stay unmatched
for the user to pick.

---

## Meeting tasks

### `GET /api/analyses/<id>/tasks`

```json
{ "tasks": [
  { "id": 3, "analysis_id": "761355ed…", "text": "update the release notes and the pricing page",
    "owner_name": "Priya", "contact_id": null, "contact_name": null, "contact_email": null,
    "due": "Thursday afternoon", "evidence": "Priya will update the release notes…",
    "status": "proposed" }
] }
```

| `status` | Meaning |
| --- | --- |
| `proposed` | Extracted from the meeting |
| `approved` | Confirmed by the user |
| `drafted` | Included in an unsent email draft |
| `emailed` | Its email was sent |
| `failed` | Its email failed to send |
| `dismissed` | Hidden and excluded from emails |
| `done` | Marked complete |

### `POST /api/analyses/<id>/tasks/rematch`

Re-run owner matching for unassigned tasks (e.g. after adding contacts).
Manual assignments are kept. Returns `{"tasks": [...]}`.

### `PATCH /api/tasks/<id>`

Edit a task. Used by the task board and `@mention` reassignment.

```json
{ "contact_id": 2 }
```

| Field | Notes |
| --- | --- |
| `text` | Cannot be blank |
| `due` | Free text as spoken ("Friday", "end of month") |
| `owner_name` | Defaults to the contact's name when `contact_id` is set |
| `contact_id` | A contact id, or `null` to unassign; unknown ids → `400` |
| `status` | `proposed` · `approved` · `dismissed` · `done` (email states are set by the server) |

---

## Emails

Drafting and sending are separate on purpose: **a draft is never sent without
an explicit user confirmation**, and only to a saved contact.

### `POST /api/analyses/<id>/emails/draft`

Create one draft per person from the meeting's tasks. Optional
`{"contact_ids": [1, 2]}` limits it to those people. Dismissed and emailed
tasks are skipped; re-drafting replaces that person's unsent drafts.

```json
{ "drafts": [
  { "id": 7, "analysis_id": "761355ed…", "contact_id": 1, "contact_name": "Sarah Khan",
    "to_addr": "sarah.khan@example.com", "subject": "Your action items from Weekly product sync",
    "body": "Hi Sarah,\n\nHere are the 2 tasks assigned to you…", "task_ids": [1, 4],
    "status": "drafted", "error": null, "created_at": "…", "sent_at": null }
], "unassigned": 1 }
```

### `GET /api/emails?analysis=<id>`

All drafts and sent emails for a meeting: `{"emails": [...]}`. `400` without
`analysis`.

### `PATCH /api/emails/<id>`

Edit an unsent draft: `{"subject": "…", "body": "…"}`. `409` once sent.

### `POST /api/emails/<id>/send`

**The only endpoint that sends email.**

```json
{ "confirm": true }
```

| Code | When |
| --- | --- |
| `200` | Sent (`status: "sent"`, tasks → `emailed`), or logged in dry-run mode (`status: "dry-run"`, tasks unchanged) |
| `400` | `confirm` is not `true` |
| `409` | Already sent, or the recipient is no longer a saved contact with that address |
| `429` | More than 20 emails in the last minute |
| `502` | SMTP failure — email and tasks marked `failed`; `error` explains why |

While `EMAIL_DRY_RUN=true` (the default) nothing is delivered.

---

## `GET /api/health`

```json
{
  "status": "ok",
  "whisper_model": "base",
  "gemini_key_configured": true,
  "web_search_enabled": true,
  "smtp_configured": false,
  "email_dry_run": true,
  "jobs": { "running": 0, "retained": 3 }
}
```

Only reports whether keys and SMTP are **set** — secrets are never returned.

---

## Errors

Transport-level problems use HTTP status codes; pipeline failures return
`200` with `status: "error"` on the job, because the *request* succeeded — the
*job* failed.

| Message contains | Cause |
| --- | --- |
| `GOOGLE_API_KEY is missing` | No key in `.env` |
| `rate limit reached` | Per-minute Gemini limit — wait and retry |
| `no quota left` | The key's quota for that model or feature is used up |
| `temporarily overloaded` / `did not respond in time` | Gemini busy; retried automatically first |
| `Whisper is not installed` | Dependencies not installed |
| `No speech was detected` | Silent or music-only audio |
| `No such file` | Local path not found on the server |
| `SMTP login failed` | Wrong SMTP password (Gmail needs an App Password) |
| `Email is not configured` | SMTP settings missing from `.env` |
| yt-dlp messages | Private, age-restricted, or region-locked video |

Gemini calls retry rate limits, 5xx errors, and timeouts up to three times
with backoff. The agent then falls back from `GEMINI_AGENT_MODEL` to
`GEMINI_MODEL`. The frontend maps messages to remedies via `ERROR_HINTS` in
`main.js`.

---

## Retention

- **Running jobs** live in memory; finished ones are evicted after 2 hours
  (max 50 retained).
- **Finished analyses, workspaces, contacts, tasks, and emails** are stored in
  SQLite at `DB_PATH` (default `data/app.db`).
- **The Q&A index** is stored in `CHROMA_DIR` (default `data/chroma/`).
- **Uploads and downloaded audio** stay in `downloads/` until you delete them.

---

## Client example

```python
import time, requests

BASE = "http://127.0.0.1:5000"

# Upload a meeting recording and analyze it in meeting mode.
with open("weekly-sync.mp4", "rb") as fh:
    up = requests.post(f"{BASE}/api/upload", files={"file": fh}).json()
job_id = requests.post(f"{BASE}/api/analyze",
                       json={"url": up["path"], "kind": "meeting", "title": up["name"]}).json()["job_id"]

while (job := requests.get(f"{BASE}/api/jobs/{job_id}").json())["status"] == "running":
    print(f"{job['percent']:3}%  {job['message']}")
    time.sleep(2)

for task in job["tasks"]:
    print(task["contact_name"] or task["owner_name"] or "Unassigned", "—", task["text"])

# Draft emails; review them, then send each one explicitly.
drafts = requests.post(f"{BASE}/api/analyses/{job_id}/emails/draft", json={}).json()["drafts"]
for draft in drafts:
    print(draft["to_addr"], draft["subject"])
    # requests.post(f"{BASE}/api/emails/{draft['id']}/send", json={"confirm": True})
```
