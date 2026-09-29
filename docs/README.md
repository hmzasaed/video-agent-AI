# Documentation

Reference material for the AI Video Assistant. Start with whichever matches
what you are trying to do.

| Document | Read it when you want to… |
| --- | --- |
| [PRD.md](PRD.md) | Understand what the product does, who it is for, and what counts as done |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Understand how the system is put together and why |
| [DESIGN.md](DESIGN.md) | Work on the UI — tokens, landing page, components, motion, accessibility |
| [API.md](API.md) | Call the backend from your own client |
| [SETUP.md](SETUP.md) | Get the project running on a new machine |
| [TESTING.md](TESTING.md) | Run or extend the test suites |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Fix a specific error you are seeing |
| [SECURITY.md](SECURITY.md) | Understand the trust boundaries and what not to expose |
| [DECISIONS.md](DECISIONS.md) | Know why something was built the way it was |
| [ROADMAP.md](ROADMAP.md) | See what is planned and what is deliberately out of scope |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Add code to the project |

The top-level [../README.md](../README.md) is the overview: screenshots, the
end-to-end workflow, install, run, and the shape of the API. Screenshots live
in [screenshots/](screenshots/).

## Conventions used in these docs

- **Stage** means one of the pipeline steps: download, transcribe,
  summarize, extract, index — plus **tasks** for meetings.
- **Workspace** means a group of analyzed videos the research agent works
  across; its videos are referred to as V1, V2, ….
- **Draft** means an unsent task email; only the user can send it.
- **Job** means one analysis run, identified by a `job_id`.
- Paths are relative to the repository root.
- Anything marked *planned* does not exist yet.

## Keeping these current

These documents describe behaviour that is covered by tests. If you change
the pipeline stages, the job lifecycle, or the API shape, update
[ARCHITECTURE.md](ARCHITECTURE.md) and [API.md](API.md) in the same change.
