# Architecture

**Last updated:** 2026-09-29

---

## 1. Shape of the system

A single Flask process serving a static frontend and a job API, with the heavy
work running on background threads.

```text
┌─────────────────────────────────────────────────────────────────┐
│ Browser — no framework, no build                                │
│  templates/index.html   landing page + 3 tools + icon sprite    │
│  static/js/main.js      Analyze: jobs, polling, results, Q&A    │
│  static/js/agent.js     Compare & Ask: workspaces, agent chat   │
│  static/js/meetings.js  task board, @mentions, contacts, email  │
│  static/js/site.js      scroll reveals, nav, mobile menu        │
└───────────────┬─────────────────────────────────────────────────┘
                │ HTTP (JSON)
┌───────────────▼─────────────────────────────────────────────────┐
│ app.py — Flask                                                  │
│  jobs:       /api/upload · /api/analyze · /api/jobs/<id>[/…]    │
│  history:    /api/analyses[/<id>]                               │
│  agent:      /api/workspaces[/<id>/{videos,compare,chat}]       │
│  meetings:   /api/contacts · /api/tasks · /api/emails[/…/send]  │
│  JOBS (in memory, running) ──on finish──► SQLite (core/db.py)   │
└───────┬───────────────────────┬──────────────────────┬──────────┘
        │ worker thread         │ per question         │ on user confirm
┌───────▼──────────────────┐ ┌──▼─────────────────┐ ┌──▼──────────────┐
│ Pipeline                 │ │ core/agent/        │ │ core/mailer.py  │
│ audio_processor  yt-dlp  │ │  agent.py  loop    │ │  SMTP (TLS)     │
│ transcriber      Whisper │ │  tools.py  search, │ │  dry-run default│
│ summarize        map/red.│ │   summary, web,    │ └─────────────────┘
│ meeting          minutes,│ │   tasks, drafts    │
│                  tasks   │ │  compare.py        │ core/drafts.py
│ extractor        3 calls │ │ core/rag_engine.py │  one draft per
│ vector_store     Chroma  │ │  single-video Q&A  │  contact
└───────┬──────────────────┘ └──┬─────────────────┘
        └──────────┬────────────┘
         core/gemini_client.py — REST, tools, retries, model fallback
```

## 2. Why background jobs

The obvious design — one `POST /api/analyze` that returns the finished result —
does not survive contact with reality. Transcribing an hour of audio takes
minutes. A single request that long will be killed by browser timeouts, by any
reverse proxy in front of the app, and offers the user no feedback or way out.

So a request **starts** work and returns an id; the browser polls. This gives
three things the blocking version cannot: live progress, cancellation, and a
request that always returns promptly.

The cost is that the server now holds mutable state, which is why `JOBS` is
guarded and pruned.

Polling (every 2s) rather than SSE or WebSockets is a deliberate simplicity
trade — see [DECISIONS.md](DECISIONS.md#adr-002--polling-rather-than-sse-or-websockets).

## 3. The pipeline

### Stage 1 — Ingest (`utils/audio_processor.py`)

```text
URL ──yt-dlp──► title.mp3 ──pydub/ffmpeg──► mono 16 kHz WAV ──► chunk_0.wav …
```

- `bestaudio/best` is downloaded and post-processed to MP3 by FFmpeg.
- Converted to **mono, 16 kHz** because that is what Whisper consumes; feeding
  it stereo 48 kHz just makes Whisper resample it anyway.
- Split into **10-minute chunks**. This bounds peak memory and creates natural
  progress checkpoints and cancellation points.
- Video metadata (title, channel, duration, thumbnail) is captured from the
  *same* `extract_info` call that performs the download, via an `on_metadata`
  callback — no second network request.

Local files skip the download and enter at the conversion step.

### Stage 2 — Transcribe (`core/transcriber.py`)

Whisper runs **locally**. The model size comes from `WHISPER_MODEL` (default
`base`). It is loaded once per job and applied to each chunk in turn.

Two engines are supported, tried in order:

1. **`faster-whisper`** (CTranslate2, `int8`, with voice-activity filtering) —
   preferred. Faster on CPU and needs no PyTorch.
2. **`openai-whisper`** — fallback, used only if `faster-whisper` is missing
   or fails to load. Needs PyTorch.

`OSError` is caught alongside `ImportError` because a PyTorch DLL blocked by
Windows Application Control surfaces as an `OSError`, not an import error.

Two callbacks make it observable and interruptible:

- `progress(message, done, total)` — fires before each chunk.
- `should_cancel()` — checked between chunks; raises `Cancelled`.

Whisper offers no way to interrupt a single `transcribe()` call, so a chunk
boundary is the finest cancellation granularity available.

### Stage 3 — Summarize (`core/summarize.py`)

A map-reduce over the transcript:

```text
transcript ──split(6000 chars, 400 overlap)──► sections
                                                  │
                     each section ──Gemini──► detailed notes      (map)
                                                  │
              all notes ──Gemini──► title + breakdown + takeaways (reduce)
```

Two passes rather than one because a long transcript will not fit in one
request, and because asking for "notes" then "prose" produces better structure
than asking for both at once.

The reduce prompt's **length target is banded by transcript size** so short
clips do not get padded summaries. The 400-character overlap prevents a
sentence spanning a split boundary from being lost.

A 3-second sleep between section calls keeps the free Gemini tier from
rate-limiting.

### Stage 4 — Extract (`core/extractor.py`)

Three independent Gemini calls against the summary — action items, decisions,
questions. Independent rather than one combined call because a single prompt
asking for three different things reliably under-delivers on at least one, and
because parsing three sections out of one response is more fragile than making
three requests.

Each returns an exact sentinel (`No decisions found.`) when empty.

### Stage 5 — Index for Q&A (`core/vector_store.py`)

The transcript and the four generated sections are split and embedded into a
**persistent local Chroma collection** at `data/chroma/` (`CHROMA_DIR`).

```text
transcript ──split(900, overlap 150)──┐
summary / actions / decisions / Qs ──split(1500, overlap 150)──┤
                                                               ▼
              Chroma "video_chunks"  — each chunk tagged {doc_id: job_id, section}
```

- Embeddings use Chroma's built-in ONNX **all-MiniLM-L6-v2**. It runs locally,
  needs no API key and no PyTorch, and downloads once (~80 MB).
- Transcript chunks are small so a hit points at one passage; the generated
  sections are already dense, so they split less.
- A failure here **does not fail the job.** The analysis is still returned,
  with `qa_ready: false` and a `qa_error` explaining why.

### Meeting mode (`core/meeting.py`)

`POST /api/analyze` with `"kind": "meeting"` runs the same pipeline plus:

```text
transcript ──► meeting_minutes()  attendees · agenda · discussion · decisions · next steps
           ──► extract_tasks()    JSON: [{task, owner, due, evidence}] + people mentioned
           ──► match_tasks()      owner name → saved contact
```

- Tasks are extracted from the **transcript** (plus the minutes for context),
  not the summary, so owner names survive.
- Owner matching: exact full name → alias → unique first name → `difflib`
  fuzzy match (≥ 0.85). Ambiguous first names are left for the user.
- Whisper does not identify speakers, so owners come from names people say,
  not from who spoke. Speaker diarization is on the roadmap.
- The minutes are indexed as their own `minutes` section for Q&A.

### Answering questions (`core/rag_engine.py`)

`POST /api/jobs/<id>/ask` runs retrieval-augmented generation:

1. Build a retrieval query from the question, plus the previous question so a
   bare follow-up like "why?" still retrieves well.
2. Search Chroma for the top `RAG_TOP_K` (default 6) chunks **filtered to this
   job's `doc_id`**, so answers never draw on another video.
3. Send Gemini the numbered excerpts, up to 4 earlier turns, and rules: answer
   only from the excerpts, say so if they don't contain the answer, cite `[n]`.
4. Return `{answer, sources}`, where each source `id` matches a citation.

Because the index is on disk, Q&A keeps working for a job that has been pruned
from memory or survives a server restart, as long as the browser still holds
its `job_id`.

### Research agent (`core/agent/`)

`POST /api/workspaces/<id>/chat` runs a **native Gemini function-calling loop**
— no agent framework:

```text
system prompt (workspace list, answer policy) + history + question
   └─► Gemini (GEMINI_AGENT_MODEL) ──functionCall──► run tool ──functionResponse──┐
            ▲                                                                    │
            └────────────────────────── repeat, max AGENT_MAX_STEPS ◄────────────┘
   └─► final text answer with [V1-3] / [V1] / [W2] citations
```

| Tool | Implementation |
| --- | --- |
| `search_videos` | `vector_store.search(doc_ids, …)` with a Chroma `$in` filter over the workspace's ids |
| `get_summary` | Stored summary or minutes from SQLite |
| `web_search` | A **separate** Gemini call with `tools: [{google_search: {}}]`; sources read from `groundingMetadata` |
| `list_tasks`, `draft_task_emails` | SQLite tasks; drafts via `core/drafts.py` |
| `list_videos` | Workspace listing |

- **Answer policy:** videos first; web only for gaps or current facts; never
  present web content as something a video said; refuse rather than guess.
- **Citations:** each retrieved excerpt gets an id (`V2-3`), each summary its
  video id (`V2`), each web source `W1`…, registered on a per-request
  `ToolContext`, so every id in the answer maps to a returned source.
- **Safety:** there is no send tool; tool output and transcripts are marked as
  data, not instructions; drafts can only target saved contacts.
- **Resilience:** Gemini calls retry 429/5xx/timeouts with backoff, then fall
  back from `GEMINI_AGENT_MODEL` to `GEMINI_MODEL`. An exhausted quota fails
  fast (no retries) and falls back once. After a web-search quota failure the
  tool short-circuits for the rest of that answer.
- **Comparison** (`compare.py`): one Gemini call over the stored summaries,
  cached on the workspace until its videos change.

### Email (`core/drafts.py`, `core/mailer.py`)

```text
tasks (with contact) ──draft_emails()──► one draft per contact (status: drafted)
      user reviews/edits in the UI
      user clicks Send → confirm dialog → POST /api/emails/<id>/send {confirm: true}
            └─► recipient still a saved contact with that address?
            └─► under 20 sends/minute?
            └─► mailer.send(): dry-run log, or SMTP (STARTTLS / SSL on 465)
```

Drafting and sending are deliberately separate so neither the agent nor a
prompt-injected transcript can send anything.

### Persistence (`core/db.py`)

Stdlib `sqlite3` at `DB_PATH` (default `data/app.db`), one short-lived
connection per call, so it is safe from request and worker threads.

| Table | Holds |
| --- | --- |
| `analyses` | Finished jobs: kind, source, title, metadata, transcript, summary, minutes, extractions, `qa_ready` |
| `workspaces`, `workspace_videos` | Workspaces, their ordered videos, cached comparison |
| `contacts` | Name, unique email (case-insensitive), aliases |
| `tasks` | Meeting tasks: text, owner, contact, due, evidence, status |
| `emails` | Drafts and sent mail: recipient, subject, body, task ids, status, error |

A job is persisted **before** it flips to `done`, so a client never sees a
finished meeting without its tasks. `GET /api/jobs/<id>` falls back to the
database once a job leaves memory.

## 4. The job record

One dict per analysis, the complete contract between backend and frontend:

```python
{
  "id": "7c273e66…",          "source": "https://youtu.be/…",
  "status": "running",         # running | done | error | cancelled
  "kind": "video",             # video | meeting
  "stage": "transcribe",       # queued|download|transcribe|summarize|extract|tasks|index|done|error|cancelled
  "message": "Transcribing chunk 2/5...",
  "percent": 42,
  "metadata": {"title": …, "uploader": …, "duration": …, "thumbnail": …},
  "transcript": "…",  "summary": "…",
  "action_items": "…", "decisions": "…", "questions": "…",
  "minutes": "…", "people": […], "tasks": […],   # meetings only
  "qa_ready": True,            # the Q&A index was built
  "qa_error": None,            # why not, when qa_ready is False
  "error": None,
  "cancel_requested": False,
  "started_at": "2026-09-28T01:03:17",
  "finished_at": "2026-09-28T01:10:31",
}
```

`GET /api/jobs/<id>` returns a **copy** taken under the lock, so a response can
never be serialized mid-mutation by the worker thread.

### Progress weighting

Stage completion percentages reflect measured time, not stage count:

| Stage | Completes at | Rationale |
| --- | --- | --- |
| download | 15% | Network + FFmpeg, roughly fixed |
| transcribe | 70% | Dominates runtime; sub-divided per chunk |
| summarize | 88% | A few API calls |
| extract | 96% | Three API calls |
| index | 100% | Local embedding, a few seconds |

Meetings use `download 12 · transcribe 62 · summarize 80 · extract 88 ·
tasks 94 · index 100` to make room for the minutes and tasks calls.

Within transcription, percent interpolates across the 15→70 band by chunk.

## 5. Concurrency

| Concern | Handling |
| --- | --- |
| Shared `JOBS` dict | Every read and write under `JOBS_LOCK` |
| Response consistency | Snapshot copied under the lock before serializing |
| Flask serving during work | `threaded=True`, so polls are answered mid-job |
| Reloader duplicating jobs | `use_reloader=False` — the reloader forks and would double-run threads |
| Unbounded memory | `_prune_jobs()` drops finished jobs after 2h, caps at 50 |
| Cancellation | Cooperative flag checked at stage and chunk boundaries |

Worker threads are daemons: they do not block process exit.

### Why threads and not a process pool

Whisper and the Gemini calls are both dominated by waiting — on the model's own
C/CUDA code (which releases the GIL) and on network I/O. Threads are sufficient
and avoid serializing large transcripts across a process boundary.

## 6. Frontend

No framework, no build step, no dependencies. Plain files served statically:
the template, one stylesheet, two scripts (`main.js` for the analyzer,
`site.js` for landing-page behaviour), and SVG/PNG assets in `static/img/`.

| Concern | Approach |
| --- | --- |
| Rendering model output | Hand-rolled markdown renderer (~50 lines) in `main.js` |
| XSS | **All** model and transcript text passes through `escapeHtml` before reaching `innerHTML`. Covered by tests. |
| State | Module-scoped `latestJob` / `currentJobId`; no store |
| Persistence | `localStorage` for theme and recent sources only, every access in try/catch |
| Theming | CSS custom properties, swapped by `data-theme` on `<html>`; an inline head script applies the saved or system theme before first paint |
| Motion | CSS transitions/keyframes plus an `IntersectionObserver` scroll reveal in `site.js`; all disabled under `prefers-reduced-motion` |

The markdown renderer exists because the model emits markdown and the
alternative — a CDN library — adds a network dependency and a much larger
attack surface for one screen's worth of formatting. It handles headings,
bold, italic, inline code, and both bullet and numbered lists. Anything else
renders as a paragraph.

See [DESIGN.md](DESIGN.md) for the visual and interaction layer.

## 7. Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `GOOGLE_API_KEY` | *(required)* | Gemini auth; absence fails at the summarize stage |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model used for summary, extraction, and Q&A |
| `WHISPER_MODEL` | `base` | Whisper size — `tiny`…`large` |
| `CHROMA_DIR` | `data/chroma` | Where the Q&A index is stored |
| `RAG_TOP_K` | `6` | Chunks retrieved per question |
| `GEMINI_AGENT_MODEL` | `gemini-3.5-flash` | Research agent and web search; falls back to `GEMINI_MODEL` |
| `AGENT_MAX_STEPS` | `6` | Tool calls per agent answer |
| `WEB_SEARCH_ENABLED` | `true` | Server-wide switch for Google Search grounding |
| `SUMMARY_PROVIDER` | `gemini` | `mistral` summarizes with Mistral via LangChain |
| `DB_PATH` | `data/app.db` | SQLite database |
| `MAX_UPLOAD_MB` | `2048` | Upload size limit |
| `EMAIL_DRY_RUN` | `true` | Log emails instead of sending |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | — | Outgoing mail; port 465 uses SSL, others STARTTLS |
| `MISTRAL_API_KEY` | — | Reserved, unused |

Loaded by `load_dotenv()` at the top of `app.py`, explicitly rather than as a
side effect of importing `gemini_client`.

## 8. Failure model

| Failure | Where caught | User sees |
| --- | --- | --- |
| Video private / unavailable | yt-dlp raises | Error + "may be private or region-locked" |
| FFmpeg missing | pydub raises | Error + "install FFmpeg" |
| Whisper not installed | Explicit `ImportError` guard | Error + `pip install` command |
| Silent audio | Empty-transcript guard | "No speech was detected" |
| Missing API key | `gemini_client` raises | Error + "add GOOGLE_API_KEY to .env" |
| Gemini 429 | Status check | Error + "wait a minute and retry" |
| Q&A indexing fails | `_index_for_qa` | Results shown; Ask tab explains Q&A is unavailable |
| Gemini overloaded / timeout | `gemini_client` retries, then falls back | Usually nothing; otherwise "Gemini is busy" |
| Quota exhausted | `QuotaError`, one fallback | "No quota left" with a link to usage |
| Web search unavailable | `web_search` tool | Agent answers from the videos and says the web couldn't be checked |
| SMTP failure | `/send` | Email and tasks marked `failed` with the reason; Retry button |
| Database write fails | `_persist` | Logged; the in-memory result is still returned |
| Anything else | Catch-all in `_run_analysis` | Message + full traceback to server log |

Every path sets `status: "error"` on the job. The worker thread never crashes
the server.

## 9. Deliberate limitations

This is a **local, single-user development server**. Before it could be
deployed:

- `app.run()` must be replaced with a WSGI server (gunicorn/waitress).
- Email sending must be restricted to authenticated users, and SMTP
  credentials moved to a secrets manager.
- In-memory jobs must move to Redis or a database — multiple workers cannot
  see each other's `JOBS` dict.
- The background thread must become a real task queue (Celery/RQ).
- `debug=True` must be turned off — it exposes an interactive debugger.
- Input must be validated against SSRF; `process_input` currently accepts any
  URL or local path from the request.

These are listed in [SECURITY.md](SECURITY.md) and [ROADMAP.md](ROADMAP.md).
