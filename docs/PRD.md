# Product Requirements — AI Video Assistant

**Status:** Implemented (v1.0)
**Last updated:** 2026-09-29

---

## 1. Problem

Long-form video — recorded meetings, lectures, conference talks, tutorials —
holds information that is expensive to retrieve. Finding one decision inside a
50-minute recording means scrubbing through it. Video is the worst possible
storage format for facts people need to look up later.

Existing options each fall short for an individual user:

- **YouTube auto-captions** give you a wall of unpunctuated text, not an answer,
  and only exist for videos YouTube has processed.
- **Paid meeting-notetaker SaaS** requires the tool to have been in the call.
  It cannot help with a video you found afterwards.
- **Pasting a transcript into a chatbot** works, but only once you have a
  transcript, and it breaks on anything longer than the context window.

## 2. Goal

Turn any video URL into a structured, readable document in one step, running
locally, with no account and no per-seat subscription.

The user pastes a link and gets back something they can read in two minutes
instead of watching for fifty.

## 3. Users

| User | Situation | What they need |
| --- | --- | --- |
| **The catcher-up** | Missed a recorded meeting | Decisions made, tasks assigned, whether anything is theirs |
| **The researcher** | Watching long talks or lectures | A skimmable breakdown, plus a searchable transcript to quote from |
| **The reviewer** | Has a backlog of videos to triage | Enough of a summary to decide whether the full video is worth their time |

All three are technical enough to run a local Python app. This is not a
consumer product; it assumes a terminal and an API key.

## 4. Scope

### In scope (v1 — built)

| # | Requirement | Status |
| --- | --- | --- |
| R1 | Accept a YouTube URL and download its audio | ✅ |
| R2 | Accept a local audio/video file path | ✅ |
| R3 | Transcribe speech locally, with no audio leaving the machine | ✅ |
| R4 | Handle videos longer than one model context via chunking | ✅ |
| R5 | Produce a structured summary with headings and takeaways | ✅ |
| R6 | Extract action items, decisions, and open questions separately | ✅ |
| R7 | Show the full transcript, searchable | ✅ |
| R8 | Report live progress during the multi-minute run | ✅ |
| R9 | Let the user cancel a running analysis | ✅ |
| R10 | Export the result as `.md` or `.txt` | ✅ |
| R11 | Explain failures in language the user can act on | ✅ |
| R12 | Work without an internet round trip for transcription | ✅ |
| R13 | Answer follow-up questions about the video, citing the passages used | ✅ |
| R14 | Keep Q&A available after the job leaves memory or the server restarts | ✅ |

### Out of scope (v1 — deliberate)

| Not building | Why |
| --- | --- |
| User accounts, multi-tenancy | Single-user local tool; auth adds no value here |
| Persistent storage of past analyses | Adds a database for a tool used a few times a day |
| Speaker diarization ("who said what") | Whisper does not do it; adding `pyannote` is a project of its own |
| Live/streaming transcription | Different architecture entirely — see [ROADMAP.md](ROADMAP.md) |
| Non-English output | Whisper transcribes many languages, but prompts assume English |
| Production deployment | `app.run()` is a dev server. See [ARCHITECTURE.md](ARCHITECTURE.md) |

## 5. User journey

```text
1. User opens http://127.0.0.1:5000
2. Pastes a YouTube URL, presses Enter
3. Within ~10s: video title, channel, duration, and thumbnail appear
4. Progress bar advances through 5 named stages with a live elapsed timer
5. User can cancel at any point
6. On completion: 6 tabs — Summary, Action items, Decisions, Questions,
   Transcript, Ask
7. User asks follow-up questions in the Ask tab and gets cited answers
8. User copies a section, or downloads the whole report (including Q&A) as .md
```

The critical design constraint is **step 3–4**: transcription takes minutes. A
spinner with no information is indistinguishable from a hang. The UI must at
all times answer "what is it doing, how far along, and can I stop it".

## 6. Requirements in detail

### R5 — Summary quality

The summary is the primary output. It must:

- Open with a title and a short overview.
- Contain a `##`-headed breakdown that follows the video's actual order.
- Include key takeaways and any concrete details (names, numbers, dates).
- **Scale its length to the source.** A 20-second clip must not produce 500
  words of padding; a 60-minute meeting must not produce 5 bullets.

That last point is a real failure mode, not a hypothetical. An early version
used a fixed "write at least 600 words" instruction and produced 480 words of
invented detail from a 35-word transcript. Length is now banded by transcript
size — see [DECISIONS.md](DECISIONS.md#adr-005--summary-length-scales-with-transcript-length).

### R6 — Extraction

Action items, decisions, and questions are extracted as three independent
calls, each with its own prompt. When a category genuinely has no content the
output is an exact sentinel string (`No actionable items found.`) rather than
an empty section or a hallucinated entry.

### R8 — Progress reporting

Progress is reported per stage, and *within* the transcription stage per audio
chunk, because transcription dominates total runtime. Percentages are weighted
to reflect real time spent, not stage count:

| Stage | Completes at |
| --- | --- |
| Download & prepare | 15% |
| Transcribe | 70% |
| Summarize | 88% |
| Extract | 96% |
| Index for Q&A | 100% |

### R11 — Actionable errors

Every known failure maps to a specific remedy shown under the error text —
missing API key, rate limit, missing FFmpeg, missing Whisper, private video,
silent audio. A raw stack trace is never the user-facing message.

### R13 — Q&A over the video

Questions are answered by retrieval-augmented generation over a local Chroma
index of the transcript and generated sections. Answers must:

- Draw **only** on the analyzed video, never another job or general knowledge.
- Say plainly when the video does not contain the answer, rather than guess.
- Cite each excerpt used as `[n]`, with the excerpt itself viewable under the
  answer.

If indexing fails, the analysis is still delivered and the Ask tab says why
Q&A is unavailable.

## 7. Success criteria

The product is working when all of the following hold:

1. A 10-minute video completes end to end without user intervention.
2. The summary is materially shorter than the transcript but retains every
   named entity, number, and decision.
3. No stage takes more than 30 seconds without updating the progress message.
4. Every error state renders a message naming a next action.
5. Cancelling stops the run within one audio chunk.
6. The result is exportable and readable outside the app.

### Measured on the current build

| Criterion | Result |
| --- | --- |
| End-to-end run | ✅ 19s video, real YouTube URL, completed |
| Summary scales to source | ✅ 35-word transcript → 96 words; 443 → 459 |
| Length fix vs. original | ✅ 105 → 459 words on the same meeting transcript |
| Error states | ✅ 8 mapped remedies, verified in tests |
| Q&A | ✅ Cited answers; unanswerable questions refused; indexing failure degrades gracefully (tested) |
| Cancellation | ✅ Verified — job reports `cancelled` |
| Export | ✅ `.md` and `.txt` |

## 8. Non-functional requirements

| Area | Requirement |
| --- | --- |
| **Privacy** | Audio never leaves the machine, and the Q&A index stays on local disk. Only *text* (transcript, and retrieved excerpts for Q&A) is sent to Gemini. |
| **Cost** | Transcription is free (local). Gemini usage is a handful of calls per video. |
| **Latency** | Dominated by Whisper. Roughly real-time on CPU for the `base` model. |
| **Resilience** | A failure in any stage fails that job only; the server stays up. |
| **Memory** | Finished jobs are evicted after 2 hours, capped at 50 retained. |
| **Accessibility** | Keyboard-navigable, screen-reader labelled, 4.5:1 text contrast in both themes, respects reduced-motion. |

## 9. Known limitations

These are accepted for v1 and documented rather than hidden:

- **Jobs are in-memory.** Restarting the server loses history. Acceptable for
  a local tool; unacceptable for deployment.
- **Cancellation is cooperative.** Whisper cannot be interrupted mid-chunk, so
  cancelling waits for the current chunk (up to ~10 minutes of audio) to finish.
- **The dev server is single-process.** Concurrent jobs share one Python GIL
  and one Whisper model load.
- **Prompts assume English.** Non-English audio transcribes but summarizes oddly.
- **No retry on Gemini rate limits.** The job fails with a message telling the
  user to wait. See [ROADMAP.md](ROADMAP.md).
