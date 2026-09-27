# AI Video Assistant

Paste a YouTube link (or a local audio/video file path) and get back a detailed
summary, action items, decisions, open questions, and a searchable transcript.

Audio is transcribed **locally** with Whisper — it never leaves your machine.
Only the resulting text is sent to Gemini for summarization.

---

## How it works

```text
YouTube URL ──► yt-dlp ──► WAV chunks ──► Whisper ──► transcript
                                                          │
                                        Gemini ◄──────────┘
                                           │
                  summary · action items · decisions · questions
```

1. **Ingest** — [`utils/audio_processor.py`](utils/audio_processor.py)
   downloads audio with `yt-dlp`, converts to mono 16 kHz WAV, splits into
   10-minute chunks.
2. **Transcribe** — [`core/transcriber.py`](core/transcriber.py) runs Whisper
   locally over each chunk.
3. **Summarize** — [`core/summarize.py`](core/summarize.py) writes per-section
   notes, then combines them into one structured summary whose length scales
   with the transcript.
4. **Extract** — [`core/extractor.py`](core/extractor.py) pulls out action
   items, decisions, and open questions.

Analysis runs as a background job with live per-chunk progress, so the browser
is never left waiting on one long request.

---

## Setup

**Prerequisites:** Python 3.10+, [FFmpeg](https://ffmpeg.org/download.html) on
your `PATH`, and a [Google AI Studio key](https://aistudio.google.com/apikey).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r Requirements.txt
Copy-Item .env.example .env
```

Set `GOOGLE_API_KEY` in `.env`. Never commit that file.

| Optional | Default | Effect |
| --- | --- | --- |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | `gemini-3.5-flash` gives richer summaries |
| `WHISPER_MODEL` | `base` | `tiny`…`large`; larger is slower but more accurate |

Full instructions in [docs/SETUP.md](docs/SETUP.md).

---

## Run

```powershell
python app.py
```

Open <http://127.0.0.1:5000>, paste a link, press **Analyze**.

The first run downloads the Whisper model (~145 MB for `base`), so it takes
noticeably longer than later runs. Transcription is the slow stage — roughly
real-time on CPU.

From the terminal instead:

```powershell
python test.py "https://www.youtube.com/watch?v=..."
```

---

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/analyze` | Start a job from `{"url": "..."}` → `202 {job_id}` |
| `GET` | `/api/jobs/<id>` | Poll progress, then results |
| `POST` | `/api/jobs/<id>/cancel` | Ask a running job to stop |
| `GET` | `/api/health` | Liveness and effective configuration |

A job carries `status` (`running`/`done`/`error`/`cancelled`), `stage`,
`message`, `percent`, `metadata`, and — once finished — `transcript`,
`summary`, `action_items`, `decisions`, `questions`.

Jobs are held in memory and lost on restart. Fine locally; see
[docs/API.md](docs/API.md) for the full reference.

---

## Tests

```powershell
python tests/test_api.py        # 41 checks — routes, job lifecycle, cancellation, errors
node tests/test_frontend.mjs    # 30 checks — markdown rendering, XSS escaping, formatting
```

Neither needs an API key, a network connection, or a downloaded video.
See [docs/TESTING.md](docs/TESTING.md).

---

## Project structure

```text
app.py                     Flask backend: routes and background job runner
templates/index.html       Page markup
static/css/style.css       Styling (dark + light themes)
static/js/main.js          Job polling, tabs, markdown rendering
core/
  gemini_client.py         Gemini REST wrapper
  transcriber.py           Whisper transcription
  summarize.py             Chunked summarization
  extractor.py             Action items, decisions, questions
utils/
  audio_processor.py       Download, convert, chunk audio
tests/                     Backend and frontend suites
docs/                      Full documentation
downloads/                 Generated media (not tracked)
test.py                    Command-line entry point
```

---

## Documentation

| Document | For |
| --- | --- |
| [PRD](docs/PRD.md) | What it does and what counts as done |
| [Architecture](docs/ARCHITECTURE.md) | How it is built and why |
| [Design](docs/DESIGN.md) | UI tokens, components, accessibility |
| [API](docs/API.md) | Endpoint reference |
| [Setup](docs/SETUP.md) | Installing on a new machine |
| [Testing](docs/TESTING.md) | Running and extending the suites |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Fixing a specific error |
| [Security](docs/SECURITY.md) | Trust boundaries and deployment gates |
| [Decisions](docs/DECISIONS.md) | Why things are the way they are |
| [Roadmap](docs/ROADMAP.md) | What's next |
| [Contributing](docs/CONTRIBUTING.md) | Adding code |

---

## Limitations

This is a **local, single-user development server**. It has no authentication,
and `POST /api/analyze` passes URLs and file paths to the pipeline unvalidated
— so it must stay bound to `127.0.0.1`. See
[docs/SECURITY.md](docs/SECURITY.md) before exposing it to anything else.

Other known limits: jobs are lost on restart, cancellation waits for the
current audio chunk, prompts assume English, and `downloads/` is never cleaned
up.
