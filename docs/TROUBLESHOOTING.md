# Troubleshooting

Find your symptom. Most problems are one of the first four.

---

## Setup

### `ffmpeg not found` / `[WinError 2] The system cannot find the file specified`

FFmpeg is missing or not on `PATH`. It is required — both the download and the
chunking stages shell out to it.

```powershell
winget install Gyan.FFmpeg      # macOS: brew install ffmpeg
```

Then **open a new terminal** and confirm `ffmpeg -version` prints a version.
An existing terminal keeps the old `PATH`.

### `Whisper is not installed`

The virtual environment is not active, or dependencies were installed
elsewhere.

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r Requirements.txt
```

Verify: `python -c "import whisper; print('ok')"`

### `GOOGLE_API_KEY is missing. Add it to your .env file.`

```powershell
Copy-Item .env.example .env
```

Add your key from <https://aistudio.google.com/apikey>, then restart the
server — `.env` is read once at startup.

Confirm it took: `curl http://127.0.0.1:5000/api/health` should report
`"gemini_key_configured": true`.

### Port 5000 already in use

Another process holds it. On macOS this is usually AirPlay Receiver
(System Settings → General → AirDrop & Handoff).

Otherwise change the port at the bottom of `app.py`:

```python
app.run(debug=True, port=5001, threaded=True, use_reloader=False)
```

---

## Running

### Stuck on "Loading the Whisper model…" for several minutes

Expected on first use of a model size. Whisper is downloading it — `small` is
~460 MB, `medium` ~1.5 GB.

Confirm it is progressing:

```bash
ls -la ~/.cache/whisper/
```

The file grows. It downloads once; later runs load it from cache in seconds.

To avoid the wait, set a smaller model in `.env`:

```ini
WHISPER_MODEL=base
```

### It's using a different Whisper model than I set

Check what is actually in effect:

```bash
curl http://127.0.0.1:5000/api/health
```

A stale `WHISPER_MODEL` line in `.env` overrides the default silently. `.env`
is only read at startup, so restart after changing it.

### Analysis is extremely slow

Transcription dominates, and it is roughly real-time on CPU with `base` — a
30-minute video takes about 30 minutes.

| Fix | Effect |
| --- | --- |
| `WHISPER_MODEL=tiny` or `base` | Largest single improvement |
| Use a CUDA GPU | Order-of-magnitude faster; needs a CUDA PyTorch build |
| Shorter videos | Cost is linear in duration |

Check GPU availability:

```python
python -c "import torch; print(torch.cuda.is_available())"
```

`False` means CPU-only. The `FP16 is not supported on CPU` warning during
transcription is expected and harmless.

### Cancel doesn't stop it immediately

By design. Whisper cannot be interrupted mid-chunk, so cancellation takes
effect at the next chunk boundary — up to ten minutes of audio. The UI shows
"Cancelling after the current step…". See
[DECISIONS.md](DECISIONS.md#adr-009--cooperative-cancellation).

---

## Video and download

### `Video unavailable` / `Private video` / `Sign in to confirm your age`

yt-dlp could not access it. Age-restricted, private, members-only, and
region-locked videos will not work.

If it affects videos that used to work, yt-dlp is out of date — YouTube changes
break it regularly:

```powershell
pip install --upgrade yt-dlp
```

This is the single most common cause of download failures.

### `No speech was detected in this video`

The audio is silent, music-only, or the speech is too quiet for Whisper. The
job stops before summarizing rather than sending an empty transcript to Gemini.

Try a larger `WHISPER_MODEL` if the speech is faint.

### The transcript is wrong or garbled

| Cause | Fix |
| --- | --- |
| Model too small | `WHISPER_MODEL=small` or `medium` |
| Heavy accent, noise, crosstalk | Larger model |
| Non-English audio | Whisper transcribes it, but the prompts assume English |
| Technical jargon, proper nouns | Inherent limitation |

---

## Summarization

### `Gemini API rate limit reached`

The free tier quota is exhausted. Wait a minute and retry. Long videos make
more calls — one per 6000-character section, plus four more.

There is no automatic retry; see [ROADMAP.md](ROADMAP.md).

### The summary is too short

Check first whether it is proportional. Length is deliberately banded by
transcript size — a 30-second clip *should* produce a short summary
([ADR-005](DECISIONS.md#adr-005--summary-length-scales-with-transcript-length)).

Genuinely too short for a long video:

1. Check the transcript tab — if transcription failed, so will the summary.
2. Try `GEMINI_MODEL=gemini-3.5-flash` for richer output.
3. Inspect `_length_target` in `core/summarize.py`.

### The summary invents things

If a short video produces a long summary full of detail that was not said,
the length banding has regressed. Verify:

```python
from core.summarize import _length_target
print(_length_target("short transcript here")[0])
```

A transcript under 300 words must select the "very short clip" rule.

### `Gemini returned no generated text`

Usually a safety filter or a malformed request. Check the server log for the
full response, and try a different `GEMINI_MODEL`.

---

## Q&A (Ask tab)

### "Q&A is unavailable for this analysis"

Indexing failed after the analysis finished; the reason follows the message.
The results are still valid — only the Ask tab is affected.

| Reason | Fix |
| --- | --- |
| Embedding model could not download | It downloads once (~80 MB) to `~/.cache/chroma` on first use — check your connection and re-run the analysis |
| `onnxruntime` / `chromadb` import error | `pip install -r Requirements.txt` inside the venv |
| Permission error on `data/chroma` | Make the folder writable, or set `CHROMA_DIR` in `.env` to one that is |

### "I could not find anything in this video related to that question"

The retrieved excerpts did not contain an answer, and the model is instructed
not to guess. Rephrase with words the speaker actually used, or check the
Transcript tab to see whether it was said at all.

### "Unknown job id" when asking

The job was pruned from memory **and** its index is missing — for example
after deleting `data/chroma/`. Re-run the analysis.

---

## Research agent

### "Your Gemini API key has no quota left for this request"

The key's quota for that model or feature is used up (free-tier daily limits
are per model). The agent automatically retries once with `GEMINI_MODEL`.
Check usage at <https://ai.dev/rate-limit>, wait for the reset, or enable
billing.

### Web search always fails

Google Search grounding needs quota on your key; free-tier keys can have none.
The agent then answers from the videos and says the web couldn't be checked.
Turn off **Web search** in the chat, or set `WEB_SEARCH_ENABLED=false`.

### "Gemini is temporarily overloaded"

The model is busy. Calls are already retried three times with backoff and
then fall back to `GEMINI_MODEL`. Wait a moment and ask again.

### Answers cite whole videos ([V1]) instead of excerpts

The agent answered from a summary rather than searching. Ask a more specific
question, or use a fuller `GEMINI_AGENT_MODEL` — lighter models search less.

---

## Meetings and email

### A task has no owner, or the wrong one

Owners come from names spoken in the meeting. Add the person to **Contacts**
(with any nicknames under "Other names"), then press **Re-match owners** — or
type `@` in the owner field and pick them.

### Emails say "Dry run" and never arrive

`EMAIL_DRY_RUN` defaults to `true`. Configure SMTP in `.env`, set
`EMAIL_DRY_RUN=false`, and restart. See [SETUP.md](SETUP.md#email-for-meeting-tasks-optional).

### "SMTP login failed"

For Gmail, use an **App Password** (needs 2-Step Verification), not your
normal password. For other providers check host, port (587 STARTTLS, 465 SSL),
and username.

### "The recipient is not a saved contact. Re-draft this email."

The contact's address changed (or was deleted) after the draft was made.
Press **Prepare emails** again to rebuild the drafts.

### Upload fails

Only mp4, mkv, mov, webm, mp3, m4a, wav, and ogg are accepted, up to
`MAX_UPLOAD_MB` (2048 MB by default).

---

## Interface

### The page loads but nothing happens on Analyze

Open the browser console (F12). Common causes:

| Console shows | Cause |
| --- | --- |
| `Cannot read properties of null` | Template and `main.js` are out of sync — an element ID was renamed. `python tests/test_api.py` catches this. |
| 404 on `/static/js/main.js` or `site.js` | Static files missing or server started from the wrong directory |
| Nothing at all | Check the server terminal for a traceback |

### Progress stops updating

The poll is failing or the server died. Check the server terminal. If the
process is gone, the job is gone with it — jobs are in memory only.

### Results are blank or show "Nothing was returned for this section"

The model returned an empty response for that section. Check the server log
and the transcript tab; if the transcript is empty the whole chain fails.

### Copy button does nothing

Clipboard access requires a secure context. `127.0.0.1` counts as secure; a
LAN IP over plain HTTP does not. Use the download buttons instead.

### Headings show in a plain system font

The page loads Space Grotesk and DM Sans from Google Fonts. If you are
offline or the request is blocked, the system-font fallback is used. The app
works the same either way.

### Theme or recent list doesn't persist

`localStorage` is unavailable — private browsing, or blocked site data. Every
access is wrapped in try/catch, so the app still works; it just forgets.

---

## Still stuck

Collect:

1. The server terminal output, including the full traceback.
2. `curl http://127.0.0.1:5000/api/health`
3. The job JSON: `curl http://127.0.0.1:5000/api/jobs/<id>`
4. Browser console output.
5. Versions: `python --version`, `pip show yt-dlp openai-whisper`, `ffmpeg -version`.

Then run both suites to see whether the problem is environmental or a code
regression:

```powershell
python tests/test_api.py
node tests/test_frontend.mjs
```

If those pass, the app's logic is intact and the problem is in the environment
or the external services.
