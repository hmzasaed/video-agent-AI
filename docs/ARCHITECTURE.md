# Architecture

**Last updated:** 2026-09-28

---

## 1. Shape of the system

A single Flask process serving a static frontend and a job API, with the heavy
work running on background threads.

```text
┌─────────────────────────────────────────────────────────────────┐
│ Browser                                                         │
│  templates/index.html  ·  static/css/style.css                  │
│  static/js/main.js — starts a job, polls it, renders the result │
└───────────────┬─────────────────────────────────────────────────┘
                │ HTTP (JSON)
┌───────────────▼─────────────────────────────────────────────────┐
│ app.py — Flask                                                  │
│                                                                 │
│  POST /api/analyze        → create job, spawn thread, return id │
│  GET  /api/jobs/<id>      → snapshot of the job dict            │
│  POST /api/jobs/<id>/cancel → set cancel flag                   │
│  GET  /api/health         → liveness + config                   │
│                                                                 │
│  JOBS: dict[str, dict]  guarded by JOBS_LOCK                    │
└───────────────┬─────────────────────────────────────────────────┘
                │ worker thread
┌───────────────▼─────────────────────────────────────────────────┐
│ Pipeline                                                        │
│                                                                 │
│  utils/audio_processor.py   yt-dlp → MP3 → mono 16kHz WAV →     │
│                             10-minute chunks                    │
│           │                                                     │
│  core/transcriber.py        Whisper, per chunk, locally         │
│           │                                                     │
│  core/summarize.py          map: notes per section              │
│                             reduce: one structured summary      │
│           │                                                     │
│  core/extractor.py          3 calls: actions / decisions / Qs   │
│           │                                                     │
│  core/gemini_client.py      REST wrapper over Gemini            │
└─────────────────────────────────────────────────────────────────┘
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

## 4. The job record

One dict per analysis, the complete contract between backend and frontend:

```python
{
  "id": "7c273e66…",          "source": "https://youtu.be/…",
  "status": "running",         # running | done | error | cancelled
  "stage": "transcribe",       # queued|download|transcribe|summarize|extract|done|error|cancelled
  "message": "Transcribing chunk 2/5...",
  "percent": 42,
  "metadata": {"title": …, "uploader": …, "duration": …, "thumbnail": …},
  "transcript": "…",  "summary": "…",
  "action_items": "…", "decisions": "…", "questions": "…",
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
| summarize | 90% | A few API calls |
| extract | 100% | Three API calls |

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

No framework, no build step, no dependencies. Three files served statically.

| Concern | Approach |
| --- | --- |
| Rendering model output | Hand-rolled markdown renderer (~50 lines) in `main.js` |
| XSS | **All** model and transcript text passes through `escapeHtml` before reaching `innerHTML`. Covered by tests. |
| State | Module-scoped `latestJob` / `currentJobId`; no store |
| Persistence | `localStorage` for theme and recent sources only, every access in try/catch |
| Theming | CSS custom properties, swapped by `data-theme` on `<html>` |

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
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model used for summary and extraction |
| `WHISPER_MODEL` | `base` | Whisper size — `tiny`…`large` |
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
| Anything else | Catch-all in `_run_analysis` | Message + full traceback to server log |

Every path sets `status: "error"` on the job. The worker thread never crashes
the server.

## 9. Deliberate limitations

This is a **local, single-user development server**. Before it could be
deployed:

- `app.run()` must be replaced with a WSGI server (gunicorn/waitress).
- In-memory jobs must move to Redis or a database — multiple workers cannot
  see each other's `JOBS` dict.
- The background thread must become a real task queue (Celery/RQ).
- `debug=True` must be turned off — it exposes an interactive debugger.
- Input must be validated against SSRF; `process_input` currently accepts any
  URL or local path from the request.

These are listed in [SECURITY.md](SECURITY.md) and [ROADMAP.md](ROADMAP.md).
