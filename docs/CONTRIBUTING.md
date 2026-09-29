# Contributing

---

## Getting set up

Follow [SETUP.md](SETUP.md), then confirm both suites pass before changing
anything:

```powershell
python tests/test_api.py        # 64 checks
node tests/test_frontend.mjs    # 35 checks
```

If they fail on a clean checkout, fix that first — you cannot tell what you
broke otherwise.

---

## Layout

```text
app.py                  Flask routes + background job runner
core/
  gemini_client.py      The only place that talks to Gemini
  transcriber.py        Whisper; owns the Cancelled exception
  summarize.py          Map-reduce summarization + length banding
  extractor.py          Action items, decisions, questions
  vector_store.py       Chroma index for Q&A (local embeddings)
  rag_engine.py         Retrieval + cited answers
utils/
  audio_processor.py    Download, convert, chunk
templates/index.html    Markup only: landing page, analyzer, icon sprite
static/css/style.css    Tokens, components, motion
static/js/main.js       Analyzer: polling, rendering, Q&A, interaction
static/js/site.js       Landing page: reveals, nav, mobile menu
static/img/             Logo, favicons, illustrations
tests/                  Both suites
docs/                   This documentation
```

**Rule:** `core/` and `utils/` must not import from `app.py`. The pipeline is
usable without Flask — `test.py` drives it directly from the command line, and
that must keep working.

---

## Conventions

The existing code is the specification. Match it.

**Python.** 4-space indent, type hints on public functions, docstrings on
anything non-obvious. Comments explain *why*, not *what*. Errors raise
`RuntimeError` with a message written for a user, not a developer.

**JavaScript.** 4-space indent, `const`/`let`, no framework, no dependencies.
Functions are small and pure where possible so the test suite can reach them.

**CSS.** Never hard-code a colour — use a token from `:root`. Add new tokens to
both the dark and light blocks. Components reference tokens only. Any new
animation must be covered by the `prefers-reduced-motion` block.

**Icons.** Add to the SVG sprite in `index.html`; never use emoji as icons.

**`main.js` vs `site.js`.** Anything the analyzer needs goes in `main.js`, and
DOM wiring stays inside its `DOMContentLoaded` block — the frontend test suite
cuts the file at that point. Landing-page behaviour goes in `site.js`.

---

## Making changes

### Adding a pipeline stage

1. Write the module in `core/`, taking `progress` and `should_cancel` callbacks.
2. Call it from `_run_analysis` in `app.py`, between `checkpoint()` calls.
3. Add it to `STAGE_WEIGHTS` and rebalance the percentages.
4. Add a stepper `<li id="step-x">` to the template and the stage to
   `STAGE_ORDER` in `main.js`.
5. Add fields to the job dict in `analyze()`.
6. Update [ARCHITECTURE.md](ARCHITECTURE.md) and [API.md](API.md).

### Adding a result tab

1. Add `<button role="tab" data-tab="x" aria-controls="panel-x">` to the tablist.
2. Add `<div id="panel-x" class="panel" role="tabpanel">`.
3. Populate it in `renderResults()` — **via `renderMarkdown()`**.
4. Add the ID to the element list in `tests/test_api.py`.

Tab wiring, keyboard navigation, and copy all work generically.

### Changing a prompt

Prompts live in `core/summarize.py`, `core/extractor.py`, and
`core/rag_engine.py`. They are the
product — a prompt change is a behaviour change.

Test both ends of the length range before and after
(see [TESTING.md](TESTING.md#summary-length)):

| Transcript | Expected |
| --- | --- |
| ~35 words | Short, faithful, nothing invented |
| ~443 words | Detailed and structured |

A short clip producing 400+ words is a regression. This has happened before —
[ADR-005](DECISIONS.md#adr-005--summary-length-scales-with-transcript-length).

### Adding an error hint

Append `[regex, "what to do about it"]` to `ERROR_HINTS` in `main.js`, and add
a `hintFor` check to the frontend suite.

---

## Security rules

Two, both non-negotiable.

**1. Everything reaching `innerHTML` is escaped.** Model output, transcripts,
and video titles are all attacker-influenceable. Use `escapeHtml()` or
`renderMarkdown()`. The five XSS tests must never be weakened or removed.

**2. Never log, return, or commit a secret.** `/api/health` reports whether a
key is configured, never the key. Before committing:

```bash
git check-ignore -v .env      # must print a .gitignore rule
git status --short            # .env must not appear
```

See [SECURITY.md](SECURITY.md).

---

## Before you commit

- [ ] `python tests/test_api.py` passes
- [ ] `node tests/test_frontend.mjs` passes
- [ ] Manually ran one real video end to end if you touched the pipeline
- [ ] `git status` shows no `.env`, no `downloads/`, no `__pycache__/`
- [ ] Docs updated if you changed the API, stages, or job shape
- [ ] New behaviour has a test

### Commit messages

```text
<area>: <what changed>

Why it changed, if not obvious.
```

Areas: `pipeline`, `api`, `ui`, `prompts`, `docs`, `tests`.

```text
prompts: scale summary length to transcript size

A fixed 600-word floor made short clips produce padded, invented
detail — a 35-word transcript yielded 480 words.
```

---

## Things that are easy to get wrong

| Trap | What happens |
| --- | --- |
| Renaming an element ID in the template only | `main.js` silently gets `null`; page half-works. The ID test catches it. |
| Adding a stage without updating `STAGE_WEIGHTS` | Progress bar jumps or stalls |
| `innerHTML` with unescaped model text | XSS |
| Blocking work without a `should_cancel` check | Cancel appears broken |
| Importing `app` from `core/` | Breaks `test.py` and the layering rule |
| Editing `.env.example` with a real key | Committed secret |
| Assuming `localStorage` works | Throws in private mode — always try/catch |
