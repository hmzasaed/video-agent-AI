# Testing

Three suites, all fast. None needs an API key, a network connection, or a
downloaded video, and none can send email.

```powershell
python tests/test_api.py                 # 174 checks — routes, jobs, meetings, contacts, email, workspaces
.venv\Scripts\python tests/test_agent.py  # 45 checks — agent loop, tools, Gemini retries/fallback
node tests/test_frontend.mjs             # 54 checks — rendering, XSS, citations, @mentions
```

`test_agent.py` imports the real `core.vector_store`, so run it with the
virtual environment's Python.

Both exit non-zero on failure, so they drop straight into CI or a pre-commit
hook.

---

## `tests/test_api.py` — backend

Replaces `utils.audio_processor`, `core.transcriber`, `core.summarize`,
`core.extractor`, the Chroma index, and the RAG answerer with stubs **before**
importing `app`, so the routes and job machinery are exercised without yt-dlp,
Whisper, Chroma, or Gemini. Runs in a few seconds.

Covers:

| Area | Checks |
| --- | --- |
| Health | Status, reported Whisper model, job counts |
| Page | Renders, `url_for` resolved, 21 required element IDs present, ARIA roles |
| Validation | Blank URL → 400, no body → 400, unknown job → 404 |
| Happy path | 202 → running → done, percent reaches 100, all result fields populated, metadata captured, timestamps set |
| Cancellation | Running job → 202 and reaches `cancelled`; finished job → 409 |
| Failures | Pipeline exception → `status: error` with the message; silent audio guarded |
| Q&A | All sections indexed; ask validation (blank, too long, unknown job, unfinished job → 400/404/409); answers with sources; pruned job with a persisted index still answers; indexing failure still finishes the job with `qa_ready: false` |
| Persistence | Finished analyses listed and served from SQLite after leaving memory (temporary `DB_PATH`) |
| Uploads | Missing file, wrong type, oversize (413), random stored name, original name kept |
| Contacts | Create, validation, case-insensitive duplicate email, update, list |
| Meetings | Meeting job completes with minutes and tasks; first-name, alias, and unmatched owners; rematch after adding a contact; task edit validation; `@mention` reassignment; dismiss |
| Email | One draft per person; dismissed tasks excluded; re-drafting replaces drafts; edit; **send refused without confirmation**; dry-run logs only; real send through a fake SMTP server; double send refused; send refused when the contact's address changed |
| Workspaces | Create/validate, auto-name, compare (cached, needs 2 videos), chat forwards videos and the web toggle, add/remove videos, delete |

### Why the element-ID checks

The template and `main.js` are coupled by element ID — `main.js` calls
`$("cancelBtn")` and gets `null` if the template drops it, failing silently at
runtime. The suite asserts every ID the JS depends on is present, so renaming
one in the template fails a test instead of breaking the page quietly.

### Expected noise

A stack trace prints during the failure-path tests. That is
`traceback.print_exc()` in the catch-all handler doing its job — the check
immediately after it reports `PASS`. Only the final count matters.

---

## `tests/test_frontend.mjs` — frontend

`main.js` is a plain `<script>` with no module system. The suite reads the
source, cuts the `DOMContentLoaded` wiring block, appends an export list, and
imports the remainder as a `data:` module with a stubbed `document` and
`localStorage`. No browser, no bundler, no test framework.

Covers:

| Area | Checks |
| --- | --- |
| **Escaping / XSS** | `<img onerror>`, `<script>` in a bullet, HTML in headings and inside bold, quote escaping |
| Markdown | `#`/`##` headings, bold, italic, inline code, `-`/`*`/numbered lists, list closing around paragraphs, empty input, real model output |
| Formatting | Durations above and below an hour, zero, elapsed clock, word counts |
| Error hints | Each mapped remedy resolves; unknown errors return none |
| Citations | `[1]` and `[1, 2]` become superscripts; non-numeric brackets untouched; escaping preserved |
| URL detection | URLs vs. Windows paths |

### The escaping tests are the important ones

Model output and transcript text both reach `innerHTML`. If `escapeHtml` is
bypassed anywhere, a video whose title or spoken content contains markup
becomes script injection. These five checks must never be allowed to fail or
be deleted.

If the suite cannot find the `DOMContentLoaded` block it exits with an
explanatory error rather than a confusing import failure.

---

## `tests/test_agent.py` — research agent

Runs the **real** agent loop and tools against a scripted fake Gemini, a fake
vector search, and a temporary SQLite database.

| Area | Checks |
| --- | --- |
| Gemini client | 503 and timeouts retried; persistent overload raises a friendly error; fallback model used; client errors not retried; exhausted quota fails fast then falls back once |
| Tool set | No tool can send email; meeting tools only with a meeting; web tool hidden when off |
| Loop | Tool calls executed and fed back; history mapped; step limit enforced with a final tool-less call; tool errors don't crash the loop |
| Citations | Excerpt ids per video (`V1-1`), summaries citable as `[V2]`, web sources deduplicated (`W1`, `W2`) |
| Web search | Uses Google Search grounding; refused when off; quota failure not retried and later searches short-circuit |
| Meetings | Tasks listed; drafts only for saved contacts; **nothing sent**; injected recipient rejected; non-meetings refused |

## Manual verification

Some things the suites cannot cover.

### Full pipeline

The only way to verify yt-dlp, FFmpeg, and Whisper actually work together:

```powershell
python app.py
# then, in another terminal:
curl -X POST http://127.0.0.1:5000/api/analyze -H "Content-Type: application/json" -d '{\"url\":\"https://www.youtube.com/watch?v=jNQXAC9IVRw\"}'
```

That URL is a 19-second public video — the shortest useful end-to-end check.
Poll `/api/jobs/<id>` until `status` is `done`.

Verified on the current build: metadata captured (title, channel, 19s,
thumbnail), 35-word transcript, 96-word summary, extraction returned correct
sentinels for a clip with no action items or questions.

### Summary length

The behaviour most likely to regress, and not assertable without live model
calls. Check both ends of the range:

| Transcript | Expected | Current |
| --- | --- | --- |
| ~35 words | Short, no invented detail | 96 words |
| ~443 words | Detailed, structured | 459 words |

A short clip producing 400+ words means the length banding in
`core/summarize.py::_length_target` has regressed. See
[DECISIONS.md](DECISIONS.md#adr-005--summary-length-scales-with-transcript-length).

### UI checklist

| Check | Expectation |
| --- | --- |
| Dark/light toggle | Survives reload; no flash of the wrong theme; contrast holds in both |
| Mobile at 375px | No horizontal scroll; Analyze full width; menu opens and closes (Esc too) |
| Landing page | Sections reveal on scroll; nav link highlights the section in view; every CTA jumps to the analyzer |
| Ask tab | Suggestions disappear after the first question; citations and sources render |
| Keyboard only | Tab reaches every control; ←/→ moves tabs; focus ring always visible |
| `Ctrl`/`Cmd`+`K` | Focuses and selects the URL field |
| Transcript search | Highlights, counts, scrolls to first match |
| Cancel mid-run | Stops within one chunk; toast appears |
| Recent pills | Persist across reload; Clear empties them |
| Upload | Drag a file onto the input card; progress shows; Analyze uses it |
| Meeting mode | Six-step progress; Minutes and Tasks tabs; owners matched |
| `@mention` | Keyboard only: focus owner, type, ↑/↓, Enter assigns, Esc restores |
| Email | Prepare → edit → Send opens the confirm dialog; dry-run notice shown |
| Compare & Ask | Create a workspace; Compare; ask with web on and off; click a citation to reveal its source |
| Reduced motion | All content visible immediately and nothing animates with the OS setting on |

---

## Adding tests

**Backend** — add a `check(label, condition, detail)` call in
`tests/test_api.py`. Use `start(url)` and `wait_for(job_id)` for job flows.
If you add a required element ID to the template, add it to the ID list.

**Frontend** — add the function name to `EXPORTS` in
`tests/test_frontend.mjs`, destructure it, and add `check(...)` calls. Only
pure functions can be tested this way; anything touching the real DOM needs a
browser.

---

## CI

```yaml
- run: pip install -r Requirements.txt
- run: python tests/test_api.py
- run: python tests/test_agent.py
- run: node tests/test_frontend.mjs
```

The backend suite imports `app`, which imports Flask and `python-dotenv` but —
because of the stubs — never loads Torch or Whisper. A slim CI install is
enough if you trim `Requirements.txt` for that job.
