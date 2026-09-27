# API Reference

Base URL when running locally: `http://127.0.0.1:5000`

All request and response bodies are JSON.

**No authentication.** The server assumes it is reachable only from localhost.
See [SECURITY.md](SECURITY.md) before exposing it.

---

## Lifecycle

Analysis is asynchronous. One request starts it, later requests poll it.

```text
POST /api/analyze  ──► 202 { job_id }
                          │
        ┌─────────────────┘
        ▼
GET /api/jobs/<id> ──► 200 { status: "running", percent, message, … }
        │  repeat every ~2s
        ▼
GET /api/jobs/<id> ──► 200 { status: "done", summary, transcript, … }
```

---

## `POST /api/analyze`

Start an analysis.

**Request**

```json
{ "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw" }
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `url` | string | yes | A `http(s)` URL, or a path to a local media file **on the server** |

**Responses**

`202 Accepted`

```json
{ "job_id": "7c273e666ea44b33b6103d871f9f3946" }
```

`400 Bad Request` — missing or blank `url`

```json
{ "error": "A video URL or local file path is required." }
```

The job is queued immediately and runs on a background thread. A `202` means
the job *started*, not that the URL is valid — a bad URL surfaces later as a
job with `status: "error"`.

```bash
curl -X POST http://127.0.0.1:5000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
```

---

## `GET /api/jobs/<job_id>`

Fetch the current state of a job. Safe to poll; 2 seconds is the interval the
bundled frontend uses.

**`200 OK`**

```json
{
  "id": "7c273e666ea44b33b6103d871f9f3946",
  "source": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
  "status": "done",
  "stage": "done",
  "message": "Analysis complete.",
  "percent": 100,
  "metadata": {
    "title": "Me at the zoo",
    "uploader": "jawed",
    "duration": 19,
    "thumbnail": "https://i.ytimg.com/vi/…/maxresdefault.jpg",
    "webpage_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"
  },
  "transcript": "Alright, so here we are, one of the elephants…",
  "summary": "📌 **All About Elephants**\n\n…",
  "action_items": "No actionable items found.",
  "decisions": "🐘 **Focus on the trunk**…",
  "questions": "No questions found.",
  "error": null,
  "cancel_requested": false,
  "started_at": "2026-09-28T01:03:17",
  "finished_at": "2026-09-28T01:10:31"
}
```

`404 Not Found` — unknown or evicted id

```json
{ "error": "Unknown job id." }
```

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `status` | string | `running` · `done` · `error` · `cancelled` |
| `stage` | string | `queued` · `download` · `transcribe` · `summarize` · `extract` · `done` · `error` · `cancelled` |
| `message` | string | Human-readable current step, e.g. `Transcribing chunk 2/5...` |
| `percent` | int | 0–100, weighted by real time per stage |
| `metadata` | object \| null | Populated during `download`; `null` for local files without metadata |
| `transcript` | string | Populated after `transcribe`; `""` before |
| `summary` | string | Markdown. Populated after `summarize` |
| `action_items` | string | Markdown, or `No actionable items found.` |
| `decisions` | string | Markdown, or `No decisions found.` |
| `questions` | string | Markdown, or `No questions found.` |
| `error` | string \| null | Set only when `status` is `error` |
| `started_at` / `finished_at` | ISO 8601 | Second precision, server local time |

Result fields fill in progressively — `transcript` is readable while the
summary is still being written.

### Status values

| `status` | Meaning |
| --- | --- |
| `running` | In progress. Keep polling. |
| `done` | Finished successfully. All result fields populated. |
| `error` | Failed. Read `error`. Nothing will change further. |
| `cancelled` | Stopped at the user's request. Partial fields may be populated. |

---

## `POST /api/jobs/<job_id>/cancel`

Ask a running job to stop.

**Responses**

| Code | Body | When |
| --- | --- | --- |
| `202` | `{"status": "cancelling"}` | Accepted; job is running |
| `404` | `{"error": "Unknown job id."}` | No such job |
| `409` | `{"error": "Job is already done."}` | Job already finished, failed, or cancelled |

Cancellation is **cooperative**. The worker checks the flag between stages and
between audio chunks, so a job mid-chunk finishes that chunk first — up to ten
minutes of audio. The response is `202`, not `200`, for exactly this reason:
the request is accepted, not completed.

Poll the job until `status` becomes `cancelled` to confirm.

---

## `GET /api/health`

Liveness and effective configuration.

**`200 OK`**

```json
{
  "status": "ok",
  "whisper_model": "base",
  "gemini_key_configured": true,
  "jobs": { "running": 0, "retained": 3 }
}
```

`gemini_key_configured` reports only whether `GOOGLE_API_KEY` is **set** — it
is never validated here, and the key is never returned.

Useful for confirming which `WHISPER_MODEL` is actually in effect, which is
easy to get wrong via `.env`.

---

## Errors

Errors carry an `error` string. Transport-level problems use HTTP status
codes; pipeline failures always return `200` with `status: "error"` on the job,
because the *request* succeeded — the *job* failed.

Common `error` values and their cause:

| Message contains | Cause |
| --- | --- |
| `GOOGLE_API_KEY is missing` | No key in `.env` |
| `rate limit reached` | Gemini quota — wait and retry |
| `Whisper is not installed` | Dependencies not installed |
| `No speech was detected` | Silent or music-only audio |
| `No such file` | Local path not found on the server |
| yt-dlp messages | Private, age-restricted, or region-locked video |

The frontend maps these to remedies via `ERROR_HINTS` in `main.js`.

---

## Job retention

In-memory only. Jobs are lost on restart.

- Finished jobs are evicted **2 hours** after they started (`JOB_TTL`).
- At most **50** jobs are retained (`MAX_JOBS`); oldest finished first.
- Pruning runs when a new job is created.

Persist anything you need on the client side — the frontend's download buttons
exist for this.

---

## Client example

```python
import time, requests

BASE = "http://127.0.0.1:5000"

job_id = requests.post(
    f"{BASE}/api/analyze",
    json={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
).json()["job_id"]

while True:
    job = requests.get(f"{BASE}/api/jobs/{job_id}").json()
    print(f"{job['percent']:3}%  {job['message']}")

    if job["status"] != "running":
        break
    time.sleep(2)

if job["status"] == "done":
    print(job["summary"])
else:
    raise SystemExit(f"{job['status']}: {job['error']}")
```
