# Roadmap

Where the project is and what would come next. Ordered by value per unit of
work, not by ambition.

---

## Shipped

| Capability | Notes |
| --- | --- |
| YouTube and local-file ingestion | yt-dlp → MP3 → mono 16 kHz WAV → 10-min chunks |
| Local Whisper transcription | `faster-whisper` with `openai-whisper` fallback; audio never leaves the machine |
| Map-reduce summarization | Handles arbitrarily long videos |
| Length banded to transcript size | [ADR-005](DECISIONS.md#adr-005--summary-length-scales-with-transcript-length) |
| Action items / decisions / questions | Three targeted extraction calls |
| Background jobs with live progress | Per-stage and per-chunk |
| Cooperative cancellation | Stops at the next chunk boundary |
| Video metadata in the UI | Title, channel, duration, thumbnail |
| Searchable transcript | Highlight + match count |
| Export | `.md` and `.txt` |
| Recent sources | `localStorage`, one click to re-run |
| Dark/light themes | Token-based, persisted, follows the OS by default, no flash |
| Landing page | Hero, how it works, features, use cases, privacy, FAQ, with scroll animations |
| Brand assets | Logo, favicons, manifest, SVG illustrations |
| Accessibility | ARIA tabs, live regions, skip link, focus states, reduced-motion |
| Actionable error messages | Eight mapped remedies |
| Q&A over the video (RAG) | Chroma + local MiniLM embeddings, cited answers, persisted index |
| Test suites | 64 backend + 35 frontend checks |

---

## Next

### 1. Retry with backoff on Gemini rate limits

**Problem.** A 429 fails the whole job after transcription has already
finished — the expensive part is wasted.

**Work.** Wrap `generate_text` in exponential backoff, three attempts, honour
`Retry-After`. Report "rate limited, retrying in Ns" through the job message
so the user sees why it stalled.

**Value.** High. This is the most likely failure on the free tier, and the
most annoying, because it discards completed work.

### 2. Clean up `downloads/`

**Problem.** Every analysis leaves an MP3, a WAV, and every chunk on disk
forever. A handful of videos is hundreds of megabytes.

**Work.** Delete chunk files after transcription; delete source media on job
completion unless a `KEEP_DOWNLOADS` flag is set.

**Value.** High — it is a bug, not a feature. Low effort.

### 3. Persist jobs

**Problem.** Restarting the server loses everything, including finished
results a user may still want.

**Work.** SQLite, one table, written on stage transitions. Add
`GET /api/jobs` to list history, and a history view in the UI.

**Value.** High. Also a prerequisite for anything multi-process.

### 4. Cache by video ID

**Problem.** Re-analyzing the same video re-downloads and re-transcribes it.

**Work.** Key the transcript on video ID; skip stages 1–2 on a hit. Re-running
only the summary becomes nearly instant, which makes prompt iteration practical.

**Value.** High for anyone tuning prompts.

---

## Later

### Timestamped Q&A citations

Q&A shipped, but sources cite text, not moments. Once Whisper segment
timestamps are kept (below), store them on each transcript chunk so a
citation can jump to that point in the video.

### Speaker diarization

"Who said what" would make meeting summaries substantially more useful —
action items could be attributed reliably. Whisper does not do this; it needs
`pyannote.audio`, a Hugging Face token, and meaningful extra runtime.

### Timestamped summaries

Whisper already returns segment timestamps; they are currently discarded.
Keeping them would let each summary bullet link to the moment in the video.
Moderate work, high payoff for long talks.

### Batch processing

Accept several URLs and process them in a queue. Mostly a UI and queue change;
the job model already supports concurrent jobs.

### Local LLM support

An Ollama backend alongside Gemini would make the whole pipeline offline and
remove the last cloud dependency. `core/gemini_client.py` is small and already
the single integration point.

### SSE instead of polling

Lower latency and fewer requests. Deliberately deferred — see
[ADR-002](DECISIONS.md#adr-002--polling-rather-than-sse-or-websockets).

### Non-English support

Whisper transcribes many languages; the prompts assume English. Detect the
language and localize the prompts.

---

## Not planned

| Not doing | Why |
| --- | --- |
| User accounts / multi-tenancy | Single-user local tool by design |
| Hosted SaaS version | Would require the entire [security checklist](SECURITY.md#deployment-checklist) and ongoing cost |
| Mobile app | The web UI is already responsive |
| Video (not audio) analysis | Different problem, vastly more compute |
| Browser extension | Adds a distribution and review burden for little gain |

---

## If this were to be deployed

Not planned, but the work is known. From
[SECURITY.md](SECURITY.md#deployment-checklist):

1. `debug=False` and a real WSGI server.
2. Authentication.
3. URL allowlisting and private-IP blocking — **the SSRF hole is the blocker**.
4. Remove or confine local-path input.
5. Jobs in Redis or a database; a real task queue.
6. Rate limiting and a duration cap.
7. CSP headers and TLS.

Items 3 and 4 are not optional. Until they are done, the app must stay bound
to `127.0.0.1`.
