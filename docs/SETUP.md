# Setup

Getting the project running on a new machine.

---

## Prerequisites

| Requirement | Why | Check |
| --- | --- | --- |
| **Python 3.10+** | Uses `str \| Path` union syntax | `python --version` |
| **FFmpeg on PATH** | Audio extraction and conversion | `ffmpeg -version` |
| **A Google AI Studio key** | Summarization, extraction, and Q&A | — |
| ~1 GB free disk | Whisper model, embedding model, downloaded audio | — |

Python 3.11 is what this project is developed against.

### Installing FFmpeg

| Platform | Command |
| --- | --- |
| Windows | `winget install Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |

Open a **new** terminal afterwards so the updated `PATH` is picked up. If
`ffmpeg -version` does not print a version, nothing below will work — the
download and chunking stages both shell out to it.

---

## Install

```powershell
git clone https://github.com/hmzasaed/video-agent-AI.git
cd video-agent-AI

python -m venv .venv
.\.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate

pip install -r Requirements.txt
```

The install pulls in PyTorch (for the `openai-whisper` fallback), which is
large (~2 GB with CUDA builds). On a slow connection expect this to take a
while. `faster-whisper` is used when available and does not need PyTorch at
runtime.

---

## Configure

```powershell
Copy-Item .env.example .env       # macOS/Linux: cp .env.example .env
```

Then edit `.env`:

```ini
GOOGLE_API_KEY=your_actual_key_here
```

Get a key at <https://aistudio.google.com/apikey>. The free tier is enough for
personal use but is rate limited — the app handles 429s with a clear message.

### Optional settings

| Variable | Default | Notes |
| --- | --- | --- |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | `gemini-3.5-flash` gives richer summaries at higher cost |
| `WHISPER_MODEL` | `base` | See the table below |
| `CHROMA_DIR` | `data/chroma` | Where the Q&A index is stored |
| `RAG_TOP_K` | `6` | Excerpts retrieved per question |
| `MISTRAL_API_KEY` | — | Reserved; not used |

#### Choosing a Whisper model

| Model | Download | Speed (CPU) | Use when |
| --- | --- | --- | --- |
| `tiny` | ~75 MB | Fastest | Testing the pipeline |
| `base` | ~145 MB | Fast | **Default.** Clear speech |
| `small` | ~460 MB | ~2× slower than base | Accents, background noise |
| `medium` | ~1.5 GB | Slow on CPU | Accuracy matters more than time |
| `large` | ~3 GB | Very slow without a GPU | GPU only |

The model downloads **once**, on first use (to the Hugging Face cache for
`faster-whisper`, or `~/.cache/whisper/` for `openai-whisper`). The first
run with a new size therefore appears to hang on "Loading the Whisper
model…" while several hundred MB download. This is normal.

`.env` is never committed — it is covered by `.gitignore`.

---

## Run

```powershell
python app.py
```

Open <http://127.0.0.1:5000>, paste a URL, press **Analyze**.

Confirm configuration took effect:

```bash
curl http://127.0.0.1:5000/api/health
```

```json
{ "status": "ok", "whisper_model": "base", "gemini_key_configured": true,
  "jobs": { "running": 0, "retained": 0 } }
```

If `gemini_key_configured` is `false`, `.env` was not found or the key is
blank. If `whisper_model` is not what you expected, check `.env` — a stale
value there silently overrides the default.

### Command line

Without the web UI:

```powershell
python test.py "https://www.youtube.com/watch?v=..."
```

---

## First run

Expect the first analysis to be much slower than later ones:

1. Whisper downloads the model (once per size).
2. `yt-dlp` downloads the audio.
3. Transcription runs — faster than real-time on CPU with `base` and
   `faster-whisper`.
4. Four to six Gemini calls.
5. The Q&A embedding model downloads once (~80 MB to `~/.cache/chroma`), then
   the video is indexed.

A 19-second clip on a cold cache took ~7 minutes, almost entirely the 460 MB
`small` model download. The same clip on a warm cache takes well under a minute.

---

## Verifying the install

```bash
# Dependencies importable
python -c "import flask, yt_dlp, pydub, faster_whisper, chromadb, dotenv, requests; print('ok')"

# FFmpeg reachable
ffmpeg -version | head -1

# Server healthy
curl http://127.0.0.1:5000/api/health
```

See [TESTING.md](TESTING.md) for the test suites.

---

## Common setup problems

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ffmpeg not found` | Not on PATH | Install, then open a new terminal |
| `Whisper is not installed` | venv not active | Activate it, re-run `pip install` |
| `GOOGLE_API_KEY is missing` | No `.env`, or blank key | Copy `.env.example`, add the key |
| Stuck on "Loading the Whisper model" | Model downloading | Wait; check `~/.cache/whisper/` grows |
| Port 5000 in use | Another process (on macOS, AirPlay) | Change the port in `app.py` |

More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
