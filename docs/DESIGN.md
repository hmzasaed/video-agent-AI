# Design

The UI layer: visual language, page structure, components, motion, and
accessibility.

**Last updated:** 2026-09-29 (research agent, meetings & email)
**Files:** [`templates/index.html`](../templates/index.html) ·
[`static/css/style.css`](../static/css/style.css) ·
[`static/js/main.js`](../static/js/main.js) ·
[`static/js/site.js`](../static/js/site.js) · [`static/js/agent.js`](../static/js/agent.js) · [`static/js/meetings.js`](../static/js/meetings.js) ·
[`static/img/`](../static/img/)

---

## 1. Design principles

**1. The wait is the product.** Most of the user's time is spent watching a
progress bar. That screen gets as much design attention as the results — named
stages, per-chunk messages, elapsed time, percentage, and a cancel button.
A spinner alone is indistinguishable from a crash.

**2. The tool is one click away.** The page is a full landing page, but the
analyzer sits directly under the hero, and every call to action jumps to it.
Nothing about the marketing sections gets in the way of pasting a link.

**3. Never show a raw error.** Every failure the system knows about is paired
with the action that fixes it.

**4. Dense output needs structure, not decoration.** The result is hundreds of
words plus a full transcript. Tabs beat one long scroll; the transcript gets
its own search.

**5. Honest content.** No invented testimonials, user counts, or customer
logos. Every claim on the page describes something the code actually does.

**6. No build step.** Plain HTML, CSS, and JS. Anyone can open the files and
change them without installing a toolchain.

## 2. Visual direction

Chosen with the `ui-ux-pro-max` design-system search for an AI productivity
tool, then adjusted for contrast:

| Aspect | Choice |
| --- | --- |
| Style | Clean, modern product site: solid surfaces, 1px borders, soft glows only in the hero and CTA |
| Palette | Teal primary with a cyan second tone; orange accent used sparingly |
| Type | **Space Grotesk** for headings, **DM Sans** for body text |
| Icons | Inline SVG sprite (Lucide, ISC licence) — no emoji, no icon font |
| Page pattern | Product demo + features: hero → analyzer → how it works → features → use cases → privacy → FAQ → CTA |

## 3. Tokens

All defined on `:root` in `style.css` and re-declared under
`:root[data-theme="light"]`. Never hard-code a colour in a component.

### Colour

| Token | Dark | Light | Used for |
| --- | --- | --- | --- |
| `--bg` | `#0a1214` | `#f6fbfa` | Page background |
| `--bg-alt` | `#0d171a` | `#ecf7f5` | Alternating section bands |
| `--card` | `#0f1b1e` | `#ffffff` | Card surfaces |
| `--card-2` | `#142427` | `#f3f9f8` | Inset surfaces — inputs, code, transcript |
| `--border` | `#21373b` | `#d3e8e4` | Borders and dividers |
| `--border-strong` | `#2f4b50` | `#a9d2cb` | Hover borders, emphasis |
| `--text` | `#e3f1ef` | `#0f3b38` | Body text |
| `--text-muted` | `#93aba8` | `#4a5d5b` | Secondary text, labels |
| `--primary` | `#2dd4bf` | `#0f766e` | Actions, active state, focus |
| `--primary-2` | `#22d3ee` | `#0e7490` | Second gradient stop |
| `--on-primary` | `#042f2e` | `#ffffff` | Text on primary buttons |
| `--accent` | `#fb923c` | `#c2410c` | Use-case icons, small highlights |
| `--success` / `--warning` / `--danger` | green / amber / red | darker variants | State colours |
| `--highlight` | orange 35% | orange 25% | Transcript search matches |

`--primary` is lighter in dark mode and darker in light mode so text and
buttons hold at least 4.5:1 contrast on their surface in both themes.

### Type

| Token | Value |
| --- | --- |
| `--font-display` | Space Grotesk → DM Sans → Segoe UI |
| `--font` | DM Sans → Segoe UI → system sans |
| `--mono` | JetBrains Mono → Cascadia Code → Consolas |

Hero `h1` is `clamp(2.3rem, 5.2vw, 3.9rem)`; section headings
`clamp(1.8rem, 3.6vw, 2.6rem)`. Body is `1rem`/`1.6`. Fonts load from Google
Fonts with `display=swap`, falling back to system fonts if blocked.

### Space, shape, motion

| Token | Value |
| --- | --- |
| `--space-1` … `--space-24` | `0.25rem` grid: 4, 8, 12, 16, 24, 32, 48, 64, 96 px |
| `--radius-lg` / `--radius` / `--radius-sm` | `20px` cards · `14px` panels · `10px` controls; `999px` pills |
| `--dur-fast` / `--dur` / `--dur-slow` | `150ms` hover · `240ms` state · `600ms` reveals |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` for entrances |
| `--nav-h` | `68px` (`60px` on phones) — sticky offsets use it |

## 4. Page structure

```text
┌──────────────────────────────────────────────────────┐
│ [logo] AI Video Assistant   How · Features · …  ☀ [Try it now]   sticky nav
├──────────────────────────────────────────────────────┤
│ Turn any video into notes you can act on   ┌───────┐ │  hero
│ lead text                                  │mockup │ │  + floating cards
│ [Analyze a video →] [See how it works]     └───────┘ │
├──────────────────────────────────────────────────────┤
│ Works with: YouTube · Local files · Whisper · …      │  strip
├──────────────────────────────────────────────────────┤
│ #app — input card · progress card · results card     │  the analyzer
├──────────────────────────────────────────────────────┤
│ #how — five step cards                               │
│ #features — bento grid                               │
│ use cases — meetings · lectures · podcasts · tutorials
│ #privacy — copy + data-flow diagram                  │
│ #faq — accordion                                     │
│ CTA band → footer                                    │
└──────────────────────────────────────────────────────┘
```

Content width is `1160px`; the analyzer and FAQ narrow to `900px` for
readable line lengths.

### Images and brand assets

| File | Purpose |
| --- | --- |
| `static/img/logo.svg` | Logo mark — play triangle, summary lines, sparkle |
| `static/img/favicon.svg`, `favicon.ico` | Browser tab icons (ICO holds 16/32/48 px) |
| `static/img/apple-touch-icon.png`, `icon-512.png` | Home-screen and manifest icons |
| `static/img/hero-mockup.svg` | Hero product illustration |
| `static/img/privacy.svg` | Data-flow diagram: what stays local, what goes to Gemini |
| `static/site.webmanifest` | Name, icons, and theme colour for installation |

All illustrations are SVG with a `<title>`/`<desc>` and a descriptive `alt`.

## 5. Analyzer components

### Input

A field with a leading link icon and the Analyze button. Focus draws a
4px `--primary-soft` ring and tints the icon. Below it: a "transcribed locally"
chip and a hint with the `Ctrl`+`K` shortcut.

Validation is **soft**: typing something that is neither a URL nor a path
tints the border amber. It never blocks submission — the backend decides.

### Recent sources

Up to 6 pills from `localStorage`, most recent first, deduplicated. Clicking
one re-runs it immediately. The `https://www.` prefix is stripped for display
but the full URL stays in `title`.

### Video metadata

Thumbnail (128×72), title, then channel · duration. Appears as soon as yt-dlp
reports it — the earliest confirmation that the app got the *right* video.
Hidden for local files.

### Progress

| Element | Purpose |
| --- | --- |
| Spinner + message | What is happening, per chunk |
| Percent · elapsed | How far and how long — tabular numerals so digits don't jitter |
| Cancel | A way out |
| Bar | Gradient fill with a moving sheen, so it never looks frozen between updates |
| Stepper | Five numbered dots joined by a line: idle, active (pulses), done (checkmark) |

The bar is a real `role="progressbar"` with a live `aria-valuenow`.

### Tabs

Six tabs with icons: Summary, Action items, Decisions, Questions, Transcript,
Ask. `role="tablist"` with roving `tabindex`; ←/→ move between them.
`aria-selected` is also the CSS hook, so visual and accessible state cannot
drift apart. The tab bar is sticky just below the nav.

### Transcript

Search box with a leading icon. Matches are wrapped in `<mark>`, counted, and
the first is scrolled into view. Search runs over the **escaped** text so the
count always equals what is highlighted.

### Ask

Chat bubbles — the question on the right in a primary tint, the answer on the
left. Citations render as superscript `[n]`; a collapsible "N sources" list
under each answer shows the excerpts, tagged by section. Four suggestion chips
appear until the first question. If Q&A is unavailable, a dashed notice
explains why and the input is disabled.

### Alerts and toast

Errors go to a persistent alert with a red left border, the message, and — when
recognised — a lightbulb remedy line from `ERROR_HINTS` in `main.js`.
Transient success ("Copied", "Analysis complete") goes to a bottom toast that
auto-dismisses after 2.2s.

## 5b. Tools: Analyze · Compare & Ask · Meetings & tasks

The analyzer section holds three tools behind a pill-shaped switcher
(`.app-tabs`, full ARIA tab pattern with ←/→). Files: `main.js` (Analyze),
`agent.js` (Compare & Ask), `meetings.js` (Meetings & tasks).

```text
            ( Analyze | Compare & Ask | Meetings & tasks )
┌── Compare & Ask ─────────────────────────────────────────────┐
│ ┌ sidebar ─────────┐ ┌ workspace ──────────────────────────┐ │
│ │ Workspaces       │ │ V1 card   V2 card   V3 card         │ │
│ │ New workspace    │ │ Comparison  [Compare]               │ │
│ │ ☐ video ☐ meeting│ ├ Research agent        (o) Web search│ │
│ │ [Create]         │ │ chat · citations · sources · steps  │ │
│ └──────────────────┘ └─────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

| Component | Notes |
| --- | --- |
| **Mode toggle** (`.seg`) | Video / Meeting radio group styled as a segmented control; Meeting changes the hint and adds a Tasks step to the stepper (6 steps) |
| **Upload** | Button + hidden file input + drag-and-drop onto the input card (dashed overlay). XHR upload with a live percentage in the file chip; the chip's × clears it |
| **Sidebar layout** (`.split-app`) | 300px sticky sidebar + main column; stacks under 1024px |
| **Pick list** (`.pick-item`) | History and workspaces; checkbox variant for building a workspace; selected state uses `--primary-soft` |
| **Video card** | 16:9 thumbnail (or icon on a teal gradient), `V1` badge, kind · channel · duration, 180-character summary snippet, Open / Tasks / Remove |
| **Comparison** | Markdown in an inset panel; "Compare again" regenerates |
| **Web search switch** (`.switch`) | Checkbox styled as a toggle; turns the agent's web tool off for that conversation |
| **Agent answer** | Markdown with clickable superscript citations (`[V1-3]` excerpt, `[V1]` summary, `[W2]` web). Clicking opens the sources list and flashes the source. Below: "N video excerpts · M web sources", then "How I answered · N steps" |
| **Draft card** | Orange-tinted callout when the agent prepared email drafts: recipients, "Nothing has been sent", Review & send |
| **Task board** | Stats line, Re-match owners, Prepare emails; one row per task with a coloured left border by status |
| **Task row** | Auto-growing task text, evidence quote, owner picker, owner state (✓ email / ⚠ not in contacts), due date, status badge, dismiss/restore |
| **Owner picker** | `@` combobox (`role="combobox"`, `aria-activedescendant`, listbox of contacts with avatar initials). ↑/↓, Enter, Esc; "Unassign" option; filtering by name, word, alias, email |
| **Quick add contact** | For an unmatched name: "Add Priya as a contact" reveals an email field; saving creates the contact and assigns the task |
| **Email card** | Collapsible: recipient, status badge, editable subject and body, Save changes (only when edited), Send / Retry |
| **Confirm dialog** (`<dialog>`) | Lists every recipient and subject, states dry-run vs real delivery, Cancel / Send N. The only way to send |
| **Notices** | Dry-run notice (teal) and "email not configured" (orange) above the task board |
| **Badges** | Proposed · Draft ready · Emailed · Sent · Dry run · Send failed · Dismissed |
| **Contacts** | Add/edit form in the Meetings sidebar; list with avatar, email, aliases; delete needs a second click within 3 seconds |

**Honesty rules for these screens:** the agent never claims an email was sent;
web content is labelled and linked; tasks show the quote they came from.

## 6. Motion

| Motion | Where | Detail |
| --- | --- | --- |
| Scroll reveal | Section content | Fade up 24px, staggered 80ms by `--i`, via `IntersectionObserver` in `site.js` |
| Hero float | Mockup and floating cards | Slow 6–8s vertical bob, offset phases |
| Background drift | Hero glows | 18s radial-gradient drift (no `filter: blur` — too costly to repaint) |
| Hover lift | Cards, tiles, buttons | `translateY(-4px)`, icon tilt, arrow nudge |
| Press | Buttons | `scale(0.97)` |
| Progress | Bar and stepper | Sheen, active-step pulse, checkmark pop |
| Results | Panels, bubbles, metadata | 240ms rise-in |
| Error | Alert | 360ms shake |

Reveal content is only hidden when JavaScript is running (`.js` class set by
the head script), so the page is fully readable without JS.

`prefers-reduced-motion: reduce` turns off smooth scrolling, shows all reveal
content immediately, and collapses every animation and transition.

## 7. Interaction

| Input | Result |
| --- | --- |
| `Enter` in the URL field | Start analysis |
| `Ctrl`/`Cmd` + `K` | Focus and select the URL field |
| `←` / `→` on a tab | Previous / next tab |
| Click a recent pill | Re-run that source |
| Nav link | Smooth-scroll to the section; the link highlights while in view |
| Menu button (≤ 820px) | Open/close the nav; `Esc` or an outside click closes it |
| Theme button | Toggle light/dark; saved in `localStorage` |

During a run the input and Analyze button are disabled and the button reads
"Analyzing…", so the only enabled action is Cancel.

## 8. States

| State | Progress card | Results | Alert | Toast |
| --- | --- | --- | --- | --- |
| Idle | hidden | hidden | hidden | — |
| Running | visible | hidden | cleared | — |
| Success | hidden | visible | hidden | "Analysis complete" |
| Failure | hidden | hidden | **visible** | — |
| Cancelled | hidden | hidden | hidden | "Analysis cancelled" |

Empty sections render an italic muted line, never a blank panel.

## 9. Theming

The theme is resolved **before first paint** by an inline script in `<head>`:
saved choice from `localStorage`, otherwise the OS `prefers-color-scheme`.
This avoids a flash of the wrong theme. `site.js` keeps the
`<meta name="theme-color">` in step so the browser chrome matches.

## 10. Accessibility

- **Contrast** — body text ≥ 7:1, muted text and primary buttons ≥ 4.5:1 in
  both themes.
- **Focus** — `:focus-visible` gives a 2px offset outline on every control.
  Never removed.
- **Skip link** — "Skip to the analyzer" is the first focusable element.
- **Labels** — inputs have `.visually-hidden` `<label>`s; icon-only buttons
  (theme, menu) have `aria-label`s that update with state; decorative SVGs are
  `aria-hidden`.
- **Live regions** — the progress message, toast, match count, and chat log
  are announced politely.
- **Tabs** — full ARIA tab pattern with roving tabindex and arrow keys.
- **Menu** — `aria-expanded` and `aria-controls` on the toggle.
- **Touch** — main controls are 44–54px tall; smaller controls grow to 44px on
  coarse pointers.
- **Motion** — reduced-motion respected everywhere (see §6).

## 11. Responsive

| Breakpoint | Changes |
| --- | --- |
| ≤ 1024px | Hero stacks and centres; how-it-works 3 columns; features and use cases 2 columns; privacy stacks |
| ≤ 820px | Nav links collapse into a menu; "Try it now" hides |
| ≤ 640px | Single-column grids; full-width buttons; smaller padding; stepper labels shrink |

Verified with no horizontal scroll at 375, 768, and 1280px.

## 12. Extending it

**Adding a result tab:** add the `<button role="tab" data-tab="x">` (with an
icon from the sprite) and a matching `<div id="panel-x" role="tabpanel">`, then
populate it in `renderResults()`. Tab wiring is generic.

**Adding an icon:** add a `<symbol id="i-name" viewBox="0 0 24 24">` to the
sprite at the top of `index.html`, then use
`<svg class="icon" aria-hidden="true"><use href="#i-name"/></svg>`.

**Adding a landing section:** use `.section` (or `.section section-alt` for a
tinted band), a `.section-head` with a `.kicker`, and add `.reveal` with
`style="--i:n"` to anything that should animate in.

**Adding an error hint:** append a `[regex, "remedy"]` pair to `ERROR_HINTS`.

**Re-theming:** change the tokens on `:root`. Do not touch components.

**Rule:** any model-generated or user-supplied string reaching `innerHTML`
must pass through `escapeHtml()` or `renderMarkdown()` first. This is what the
frontend test suite checks.
