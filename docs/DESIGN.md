# Design

The UI layer: visual language, components, interaction, and accessibility.

**Last updated:** 2026-09-28
**Files:** [`templates/index.html`](../templates/index.html) ·
[`static/css/style.css`](../static/css/style.css) ·
[`static/js/main.js`](../static/js/main.js)

---

## 1. Design principles

**1. The wait is the product.** Most of the user's time is spent watching a
progress bar. That screen gets as much design attention as the results — named
stages, per-chunk messages, elapsed time, percentage, and a cancel button.
A spinner alone is indistinguishable from a crash.

**2. Never show a raw error.** Every failure the system knows about is paired
with the action that fixes it.

**3. Dense output needs structure, not decoration.** The result is ~500 words
plus a full transcript. Tabs beat one long scroll; the transcript gets its own
search.

**4. No build step.** Plain HTML, CSS, and JS. Anyone can open the files and
change them without installing a toolchain.

## 2. Tokens

All defined on `:root` in `style.css` and re-declared under
`:root[data-theme="light"]`. Never hard-code a colour in a component.

### Colour

| Token | Dark | Light | Used for |
| --- | --- | --- | --- |
| `--bg` | `#0f1117` | `#f4f6fb` | Page background |
| `--card` | `#1b1f2b` | `#ffffff` | Card surfaces |
| `--card-2` | `#222736` | `#f7f9fc` | Inset surfaces — inputs, code, transcript |
| `--border` | `#2c3243` | `#dfe4ee` | All borders and dividers |
| `--text` | `#e7e9ee` | `#1b1f2b` | Body text |
| `--text-muted` | `#9aa3b8` | `#626b80` | Secondary text, labels |
| `--primary` | `#6d8bff` | `#4f46e5` | Actions, active state, focus |
| `--primary-soft` | 14% primary | 10% primary | Tinted backgrounds, page glow |
| `--success` | `#4ade80` | `#16a34a` | Completed stages |
| `--warning` | `#fbbf24` | `#fbbf24` | Soft input validation |
| `--danger` | `#f87171` | `#dc2626` | Errors, cancel |
| `--highlight` | amber 35% | amber 45% | Transcript search matches |

The dark palette is the default because the tool is used alongside a terminal.
Light mode is not an afterthought — `--primary` shifts from a lighter blue to
indigo so contrast holds on white.

### Type, space, shape

| Token | Value |
| --- | --- |
| `--font` | Inter → Segoe UI → system sans |
| `--mono` | JetBrains Mono → Cascadia Code → Consolas |
| `--radius` | `14px` cards · `10px` controls · `999px` pills |
| `--shadow` | `0 10px 30px` at 35% dark / 8% light |

Type scale is fluid at the top: `h1` is
`clamp(1.8rem, 4vw, 2.6rem)`. Body is `1rem`/`1.6`. Secondary text is
`0.85rem`, metadata `0.8rem`.

Spacing uses a `0.25rem` grid; cards are `1.5rem` padded, `1.1rem` on mobile.

## 3. Layout

Single column, `max-width: 900px`, centred. Wider lines hurt readability of
the summary, which is the main thing being read.

```text
┌──────────────────────────────────────────┐
│                          [ ☀️ Light ]    │  topbar
│            🎥 AI Video Assistant         │  header
│         one line of explanation          │
├──────────────────────────────────────────┤
│  [ url input            ] [ Analyze ]    │  input card
│  hint · Ctrl+K                           │
│  ── Recent ────────────────  [ Clear ]   │
│  ( pill ) ( pill ) ( pill )              │
├──────────────────────────────────────────┤
│  [thumb]  Video title                    │  progress card
│           Channel · 30m 45s              │  (hidden until running)
│  ◐ Transcribing chunk 2/5   42%  01:23  [Cancel]
│  ████████████░░░░░░░░░░░░░░░░░░░░        │
│  (1 Download)(2 Transcribe)(3 …)(4 …)    │
├──────────────────────────────────────────┤
│  Summary │ Actions │ Decisions │ … │     │  results card
│  480 words · 3,204 transcribed  [Copy][.md][.txt]
│                                          │
│  rendered markdown                       │
└──────────────────────────────────────────┘
```

Only one of the progress card and results card is visible at a time.

## 4. Components

### Input

Flex row that wraps; the field is `flex: 1 1 320px` so it drops the button to
its own full-width line below ~600px. Focus draws a 3px `--primary-soft` ring.

Validation is **soft**: typing something that is neither a URL nor a path
tints the border amber. It never blocks submission — the backend decides.

### Recent sources

Up to 6 pills from `localStorage`, most recent first, deduplicated. Clicking
one re-runs it immediately. The `https://www.` prefix is stripped for display
but the full URL stays in `title`.

### Video metadata

Thumbnail (120×68, `object-fit: cover`), title, then channel · duration.
Appears as soon as yt-dlp reports it — typically within seconds, long before
the analysis finishes. This is the earliest possible confirmation that the app
got the *right* video.

Hidden entirely when there is no metadata (local files).

### Progress

| Element | Purpose |
| --- | --- |
| Spinner | Something is happening |
| Message | *What* is happening, per chunk |
| Percent | How far |
| Elapsed | How long — monospace so digits don't jitter |
| Cancel | A way out |
| Bar | At-a-glance progress, 0.4s eased |
| Step pills | Where in the overall pipeline |

Step pills have three states: idle (muted), `.active` (primary, tinted),
`.done` (green). The bar is a real `role="progressbar"` with a live
`aria-valuenow`.

### Tabs

`role="tablist"` with roving `tabindex` — the selected tab is the only one in
the tab order, and ←/→ move between them. Selection is driven by
`aria-selected`, which is also the CSS hook, so the visual and accessible
states cannot drift apart.

Sticky at the top of the results card so they stay reachable while scrolling a
long transcript.

### Transcript

Its own panel with a search box. Matches are wrapped in `<mark>` using
`--highlight`, counted, and the first is scrolled into view. Search runs over
the **escaped** text so the highlight count always equals what is marked up.

Capped at `55vh` with its own scroll, in `--card-2` with `white-space: pre-wrap`.

### Toast

A single bottom-centre pill for transient confirmations — copied, downloaded,
cancelled, complete. Slides up via `transform`, auto-dismisses after 2.2s.

Transient success goes to the toast; **failure goes to the persistent alert
box**, because errors need to stay on screen to be acted on.

### Alerts

Title, message, and — when recognised — a `💡` remedy line. The mapping lives
in `ERROR_HINTS` in `main.js`, matched by regex against the backend message.

## 5. Interaction

| Input | Result |
| --- | --- |
| `Enter` in the URL field | Start analysis |
| `Ctrl`/`Cmd` + `K` | Focus and select the URL field |
| `←` / `→` on a tab | Previous / next tab |
| Click a recent pill | Re-run that source |
| `Cancel` | Request cancellation |

During a run the input and Analyze button are disabled and the button reads
"Analyzing…", so the only enabled action is Cancel.

## 6. States

Every asynchronous surface has four: **idle**, **running**, **success**,
**failure**. Cancellation is a fifth on the progress card.

| State | Progress card | Results | Alert | Toast |
| --- | --- | --- | --- | --- |
| Idle | hidden | hidden | hidden | — |
| Running | visible | hidden | cleared | — |
| Success | hidden | visible | hidden | "Analysis complete" |
| Failure | hidden | hidden | **visible** | — |
| Cancelled | hidden | hidden | hidden | "Analysis cancelled" |

Empty sections render an italic muted line, never a blank panel.

## 7. Accessibility

- **Contrast** — body text ≥ 7:1 on its surface in both themes; muted text
  ≥ 4.5:1. `--primary` shifts between themes specifically to hold this.
- **Focus** — `:focus-visible` gives a 2px offset outline on every control.
  Never removed.
- **Labels** — the URL and search inputs have `.visually-hidden` `<label>`s.
  The thumbnail carries a descriptive `alt`.
- **Live regions** — the progress message is `role="status" aria-live="polite"`,
  so stage changes are announced. The toast and match count are too.
- **Tabs** — full ARIA tab pattern with roving tabindex and arrow keys.
- **Motion** — `prefers-reduced-motion: reduce` collapses all animation and
  transition to 0.01ms, including the spinner.
- **Semantics** — `<section aria-label>` per region; `role="alert"` on errors.

## 8. Responsive

One breakpoint, `600px`:

- Page padding `2.5rem` → `1.5rem`, cards `1.5rem` → `1.1rem`.
- Analyze becomes full width.
- Progress meta (percent/elapsed/cancel) wraps to its own row.
- Thumbnail shrinks to 88×50.
- Tabs scroll horizontally rather than wrapping.

Everything else is fluid by construction — flex wrapping and `clamp()` rather
than fixed widths.

## 9. Extending it

**Adding a result tab:** add the `<button role="tab" data-tab="x">` and a
matching `<div id="panel-x" role="tabpanel">`, then populate it in
`renderResults()`. Tab wiring is generic.

**Adding an error hint:** append a `[regex, "remedy"]` pair to `ERROR_HINTS`.

**Re-theming:** change the tokens on `:root`. Do not touch components.

**Rule:** any model-generated or user-supplied string reaching `innerHTML`
must pass through `escapeHtml()` or `renderMarkdown()` first. This is what the
frontend test suite checks.
