# Security

What this application trusts, what it exposes, and what must change before it
is reachable by anyone but you.

---

## Threat model

The app is designed as a **single-user tool bound to localhost**. That
assumption underpins everything below. It has no authentication, no
authorization, and no tenancy.

| Asset | Exposure |
| --- | --- |
| `GOOGLE_API_KEY` | Read from `.env`, sent only to `generativelanguage.googleapis.com` |
| Source audio | Never leaves the machine |
| Transcript text | Sent to Google's Gemini API |
| Q&A index | Embedded locally into `data/chroma/`, untracked, kept until deleted |
| Questions asked | Sent to Gemini with the retrieved excerpts |
| Downloaded media | Written to `downloads/`, untracked, never cleaned up |
| Job results | In memory only, lost on restart |

---

## What stays local

Transcription runs on your machine via `faster-whisper` (or `openai-whisper`
as a fallback). **Audio is never uploaded anywhere.** Q&A embeddings are also
computed locally (ONNX MiniLM) and stored on local disk. For recordings of meetings, calls, or anything
confidential, this is the property that matters most.

What *is* sent to Google is the **transcript text**, plus the section notes and
summary derived from it — in four to six requests per video. If the spoken
content itself is sensitive, that is the boundary to consider. Google's
handling is governed by the terms of whichever API tier your key belongs to;
free-tier usage may be retained and used for product improvement.

For each Q&A question, Gemini receives the question, up to six retrieved
excerpts from that video, and up to four earlier turns of the conversation.

The browser also loads the **Space Grotesk** and **DM Sans** fonts from Google
Fonts, which means the page makes requests to `fonts.googleapis.com` and
`fonts.gstatic.com`. No analysis data is included in those requests. To remove
them, self-host the fonts or delete the font `<link>` tags; the system-font
fallbacks take over.

There is no way to get summaries without sending text to a model provider.
Running a local LLM instead is on the [roadmap](ROADMAP.md).

---

## Secrets

| Rule | Where enforced |
| --- | --- |
| `.env` is never committed | `.gitignore` — `.env`, `.env.*`, with `!.env.example` |
| Keys are never returned by the API | `/api/health` reports only a boolean |
| Keys are never logged | No key appears in any print or traceback |
| `.env.example` holds placeholders only | Reviewed |

`.gitignore` also covers `.venv/`, `__pycache__/`, `downloads/`, and media
extensions. **If `.gitignore` is ever deleted, `.env` becomes stageable** —
this has happened once in this repository's history. Verify with:

```bash
git check-ignore -v .env
```

If that prints nothing, stop and restore `.gitignore` before committing.

### If a key leaks

1. Revoke it at <https://aistudio.google.com/apikey> immediately.
2. Issue a new one and update `.env`.
3. If it was committed, rewrite history — the key is compromised regardless.

---

## Cross-site scripting

The most realistic attack surface. Summaries render as HTML, and both the
transcript and the video title are **attacker-influenceable** — anyone can
publish a video whose title or spoken words contain markup.

**Control.** Every model-generated and user-supplied string passes through
`escapeHtml()` before reaching `innerHTML`. `renderMarkdown()` escapes first,
then applies formatting to already-escaped text. Transcript search highlights
against escaped text.

**Verification.** Five tests in `tests/test_frontend.mjs` cover `<img onerror>`,
`<script>` inside a list item, HTML in headings, and HTML nested in bold.
These must never be deleted.

**Rule for contributors:** if you add a surface that writes a string to
`innerHTML`, it goes through `escapeHtml()` or `renderMarkdown()`. There are no
exceptions, and no raw-HTML passthrough in the renderer by design.

---

## Server-side request forgery and path traversal

**This is the app's most significant weakness, and it is accepted only because
the server is bound to localhost.**

`POST /api/analyze` takes a `url` field and passes it to `process_input`
unvalidated. That means:

- **Any URL** is fetched by yt-dlp from the server — including
  `http://localhost:…`, private-range addresses, and cloud metadata endpoints
  such as `169.254.169.254`.
- **Any local path** is read from the server's filesystem and converted. A path
  like `C:\Users\you\Documents\private.mp4` will be processed and its contents
  transcribed and sent to Gemini.

Anyone who can reach the API can therefore read server-local media and probe
the server's network position.

**Before exposing this app to any other host**, you must:

1. Allowlist URL schemes and hostnames (e.g. YouTube domains only).
2. Resolve hostnames and reject private, loopback, and link-local ranges.
3. Drop local-path support entirely, or confine it to one allowlisted directory
   with the resolved real path checked against it.
4. Add authentication.

---

## Debug mode

`app.py` runs with `debug=True`. The Werkzeug debugger executes **arbitrary
Python from the browser** on an unhandled exception. It is protected by a PIN,
but is not a security boundary.

This must be `False` anywhere other than your own machine.

---

## Resource exhaustion

| Vector | Current state |
| --- | --- |
| Unbounded job creation | No rate limiting — each request spawns a thread and a download |
| Disk growth | `downloads/` is never cleaned; every analysis leaves MP3, WAV, and chunk files. `data/chroma/` grows with each indexed video |
| Question length | Capped at 1,000 characters; history trimmed to 4 turns of 2,000 characters |
| Memory | Bounded: jobs evicted after 2h, capped at 50 |
| Video length | Unbounded — a 10-hour video will be downloaded and transcribed |

`downloads/` should be cleared periodically. Nothing depends on its contents
after a job completes.

---

## Dependencies

`yt-dlp` needs frequent updating — it breaks whenever YouTube changes, and
stale versions are the most common cause of download failures:

```powershell
pip install --upgrade yt-dlp
```

`torch`, `openai-whisper`, and `chromadb` are large and pull in substantial transitive
dependency trees. Review advisories before deploying anything built on this.

---

## Deployment checklist

Do not skip any of these if the app will be reachable by anyone else.

- [ ] `debug=False`
- [ ] Replace `app.run()` with a WSGI server (waitress, gunicorn)
- [ ] Add authentication
- [ ] Validate and allowlist URLs; block private IP ranges
- [ ] Remove or confine local-path input
- [ ] Move jobs out of memory (Redis or a database)
- [ ] Replace background threads with a task queue
- [ ] Rate-limit `POST /api/analyze`
- [ ] Cap accepted video duration
- [ ] Add a cleanup policy for `downloads/`
- [ ] Set a `Content-Security-Policy` header
- [ ] Serve over TLS

Until all of these are done, keep it on `127.0.0.1`.

---

## Reporting

This is a personal project with no formal disclosure process. Open an issue,
or fix it directly — see [CONTRIBUTING.md](CONTRIBUTING.md).
