# Architecture Decision Records

Why things are the way they are. Each record states the decision, what it
cost, and what was rejected.

---

## ADR-001 — Background jobs instead of one blocking request

**Status:** Accepted

**Context.** The first version was a single `POST /api/analyze` that ran the
whole pipeline and returned the result. Transcription takes minutes; an hour of
audio takes longer than most browsers and every reverse proxy will wait.

**Decision.** `POST /api/analyze` creates a job, starts a background thread,
and returns `202` with a `job_id`. The client polls `GET /api/jobs/<id>`.

**Consequences.**

- Requests always return promptly. Timeouts stop being a failure mode.
- Progress and cancellation become possible at all.
- The server now holds mutable state, needing a lock and an eviction policy.
- Restarting the server loses in-flight and completed jobs.

**Rejected.** Streaming the response — most proxies buffer it, and a dropped
connection loses everything with no way to reattach.

---

## ADR-002 — Polling rather than SSE or WebSockets

**Status:** Accepted

**Context.** The client needs progress updates over several minutes.

**Decision.** Poll every 2 seconds.

**Consequences.**

- Trivial to implement and debug: `GET /api/jobs/<id>` is inspectable with
  `curl`, and any HTTP client can drive the API.
- Reconnection is free — a dropped poll just retries.
- Up to 2 seconds of staleness, and a request every 2s per active client.

**Rejected.** SSE would be more efficient and is a reasonable future change,
but it adds a streaming endpoint, connection lifecycle handling, and a
reconnection strategy to save a negligible number of requests for a local,
single-user tool.

---

## ADR-003 — Whisper runs locally; only text goes to the cloud

**Status:** Accepted

**Context.** Transcription could use a hosted API (OpenAI, AssemblyAI) instead
of running locally.

**Decision.** Transcribe locally with `openai-whisper`.

**Consequences.**

- **Audio never leaves the machine.** Recorded meetings often contain material
  that should not be uploaded to a third party. Only the transcript text is
  sent to Gemini for summarization.
- No per-minute transcription cost.
- Works offline once the model is cached.
- Slow on CPU — roughly real-time with `base` — and dominates runtime.
- Large first-run download per model size.

This is the central privacy property of the project. See [SECURITY.md](SECURITY.md).

---

## ADR-004 — Map-reduce summarization

**Status:** Accepted

**Context.** An hour of speech is far more text than one request should carry,
and a single "summarize this" call over a long transcript loses the middle.

**Decision.** Split into 6000-character sections with 400 characters of
overlap. Summarize each into detailed notes (map), then combine the notes into
one structured summary (reduce).

**Consequences.**

- Handles arbitrarily long videos.
- Natural progress checkpoints per section.
- Overlap stops a sentence spanning a boundary from being dropped.
- Cost scales linearly with length; a 3-second sleep between section calls
  keeps the free tier from rate-limiting.

**Rejected.** A single call with a very large context window — simpler, but
quality degrades badly in the middle of long inputs, and it makes progress
reporting impossible.

---

## ADR-005 — Summary length scales with transcript length

**Status:** Accepted (revises an earlier fix)

**Context.** The original pipeline produced summaries that were far too short.
Three causes: `max_output_tokens=160` per section, 1800-character sections, and
— the biggest — single-section transcripts returned the raw section notes and
skipped the expansion pass entirely.

The first fix raised the limits and always ran the expansion pass, instructing
the model to "write at least 600 words". Measured on a meeting transcript this
took the summary from 105 words to 809.

But that fixed floor then misfired at the other end. A real 19-second video
(35-word transcript) produced **480 words**, including invented detail —
"captures the full scope of the animal's physical wonder", "observers around
the world" — none of which was said. The model padded to hit the floor.

**Decision.** Band the target length by transcript size in
`core/summarize.py::_length_target`:

| Transcript | Target | Extra instruction |
| --- | --- | --- |
| < 300 words | ~half to 1× the transcript, min 60–120 | "Do not pad, speculate, or invent detail" |
| 300–1500 | 300–500 words | "Do not repeat yourself" |
| > 1500 | 600+ words | "Never respond with only a few short bullets" |

**Consequences.**

- Verified: 35-word transcript → 96 words; 443-word → 459 words.
- Faithfulness on short clips improved — the padded, speculative phrasing is gone.
- The banding is a heuristic on word count, not content density. A dense
  five-minute technical talk and a rambling five-minute vlog get the same band.

**Lesson.** "Too short" is not fixed by demanding "long". It is fixed by
making length proportional to how much was actually said.

---

## ADR-006 — Three separate extraction calls

**Status:** Accepted

**Context.** Action items, decisions, and questions could come from one prompt
returning three sections, or one JSON response.

**Decision.** Three independent calls, one per category.

**Consequences.**

- Each prompt is specific, so each category gets full attention. A combined
  prompt reliably under-delivers on at least one of the three.
- No response parsing — each call returns its section directly, avoiding the
  fragility of splitting one response or repairing malformed JSON.
- Three times the requests. Acceptable: they run against the summary, not the
  full transcript, so they are small.

---

## ADR-007 — No frontend framework and no build step

**Status:** Accepted

**Context.** The UI has one screen, five tabs, and a polling loop.

**Decision.** Plain HTML, CSS, and JavaScript. No React, no bundler, no npm.

**Consequences.**

- `python app.py` is the entire toolchain. No `node_modules`, no build.
- Anyone can open the three files and change them.
- Markdown rendering had to be hand-written (~50 lines) rather than pulled
  from a CDN — which also avoids a runtime network dependency and a much
  larger XSS surface.
- State management is manual. At roughly twice this size, that stops being a
  good trade.

---

## ADR-008 — Escape everything reaching innerHTML

**Status:** Accepted

**Context.** Summaries are markdown and must render as HTML. Transcripts and
video titles are attacker-influenceable — anyone can publish a video whose
title or spoken content contains markup.

**Decision.** Every model-generated and user-supplied string passes through
`escapeHtml()` before any HTML construction. The markdown renderer escapes
**first**, then applies formatting to the escaped text. Transcript search
highlights against escaped text too.

**Consequences.**

- Injected markup renders as visible text, never as elements.
- Enforced by five dedicated tests that must never be removed.
- The renderer cannot support raw HTML passthrough. That is the point.

---

## ADR-009 — Cooperative cancellation

**Status:** Accepted

**Context.** Users start a 40-minute video by mistake and want out. Python
threads cannot be killed.

**Decision.** A `cancel_requested` flag on the job, checked between stages and
between audio chunks via a `should_cancel` callback, raising `Cancelled`.

**Consequences.**

- Cancellation always leaves the system in a consistent state — no half-written
  job records, no orphaned model state.
- It is **not immediate**. Whisper cannot be interrupted mid-chunk, so a job
  finishes the current chunk first — up to ten minutes of audio.
- The cancel endpoint returns `202 Accepted`, not `200`, to reflect this, and
  the UI says "Cancelling after the current step…".

**Rejected.** Running the pipeline in a subprocess so it could be killed
outright. Cleaner cancellation, but it adds IPC for transcripts and loses
in-process progress reporting.

---

## ADR-010 — Configurable models via environment

**Status:** Accepted

**Context.** Whisper size trades speed against accuracy; Gemini model trades
cost against quality. Both were hard-coded.

**Decision.** `WHISPER_MODEL` and `GEMINI_MODEL` environment variables, with
the previous hard-coded values as defaults.

**Consequences.**

- Users tune for their hardware and budget without editing code.
- `GET /api/health` reports the effective values, because a stale `.env` entry
  silently changing behaviour is otherwise very hard to diagnose.

**Note.** `WHISPER_MODEL=small` was already present in a local `.env` but had
never taken effect — `transcribe_all` was always called with its default. This
decision made an existing setting real, which is itself a reason to surface
effective configuration on the health endpoint.
