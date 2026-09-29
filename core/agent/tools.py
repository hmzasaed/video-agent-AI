"""Tools the workspace agent can call.

Each tool is a Gemini function declaration plus a Python implementation that
receives a ``ToolContext``. Tools return plain dicts; anything citable is
registered on the context so the answer's ``[V1-3]`` / ``[W2]`` ids resolve to
real sources.

There is deliberately no tool that sends email.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import db, gemini_client, vector_store
from core.drafts import draft_emails

SUMMARY_LIMIT = 6000
SEARCH_K = 6
MAX_WEB_SOURCES = 6


@dataclass
class ToolContext:
    videos: list[dict]                       # workspace analyses, in order
    allow_web: bool = True
    web_unavailable: str = ""                # set after a quota/overload failure
    sources: list[dict] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    _excerpts: dict = field(default_factory=dict)  # ref -> next excerpt number
    _web_count: int = 0

    # ── helpers ──
    def ref_of(self, analysis_id: str) -> str:
        for number, video in enumerate(self.videos, start=1):
            if video["id"] == analysis_id:
                return f"V{number}"
        return "V?"

    def resolve(self, ref: str) -> dict | None:
        """Accept "V2", "v2", "2", or a raw analysis id."""
        value = str(ref or "").strip()
        if value.upper().startswith("V"):
            value = value[1:]
        if value.isdigit() and 1 <= int(value) <= len(self.videos):
            return self.videos[int(value) - 1]
        return next((v for v in self.videos if v["id"] == ref), None)

    def add_video_source(self, hit: dict) -> str:
        ref = self.ref_of(hit["doc_id"])
        number = self._excerpts.get(ref, 0) + 1
        self._excerpts[ref] = number
        source_id = f"{ref}-{number}"
        self.sources.append({
            "id": source_id, "type": "video", "video": ref,
            "title": self.resolve(ref)["title"] if self.resolve(ref) else "",
            "section": hit.get("section", ""), "text": hit["text"],
        })
        return source_id

    def add_web_source(self, title: str, url: str) -> str:
        for source in self.sources:
            if source["type"] == "web" and source["url"] == url:
                return source["id"]
        self._web_count += 1
        source_id = f"W{self._web_count}"
        self.sources.append({"id": source_id, "type": "web", "title": title, "url": url})
        return source_id


# ── Implementations ──────────────────────────────────────────────────
def list_videos(ctx: ToolContext) -> dict:
    return {"videos": [
        {"ref": f"V{n}", "title": v.get("title") or v.get("source", ""), "kind": v.get("kind", "video")}
        for n, v in enumerate(ctx.videos, start=1)
    ]}


def get_summary(ctx: ToolContext, video: str) -> dict:
    target = ctx.resolve(video)
    if target is None:
        return {"error": f"No video '{video}' in this workspace."}
    analysis = db.get_analysis(target["id"]) or {}
    body = analysis.get("minutes") or analysis.get("summary") or ""
    ref = ctx.ref_of(target["id"])
    if body and not any(s["id"] == ref for s in ctx.sources):
        # The whole summary is citable as [V1].
        ctx.sources.append({
            "id": ref, "type": "video", "video": ref, "title": analysis.get("title", ""),
            "section": "minutes" if analysis.get("minutes") else "summary",
            "text": body[:1200] + ("…" if len(body) > 1200 else ""),
        })
    return {
        "ref": ref,
        "cite_as": f"[{ref}]",
        "title": analysis.get("title", ""),
        "kind": analysis.get("kind", "video"),
        "summary": body[:SUMMARY_LIMIT] or "No summary is stored for this video.",
    }


def search_videos(ctx: ToolContext, query: str, videos: list[str] | None = None) -> dict:
    targets = [ctx.resolve(v) for v in videos] if videos else list(ctx.videos)
    ids = [t["id"] for t in targets if t and t.get("qa_ready")]
    if not ids:
        return {"results": [], "note": "None of these videos has a searchable index."}
    hits = vector_store.search(ids, query, k=SEARCH_K)
    return {"results": [
        {"id": ctx.add_video_source(hit), "video": ctx.ref_of(hit["doc_id"]),
         "section": hit["section"], "text": hit["text"]}
        for hit in hits
    ]}


def web_search(ctx: ToolContext, query: str) -> dict:
    if not ctx.allow_web:
        return {"error": "Web search is turned off for this conversation."}
    if ctx.web_unavailable:
        return {"error": f"Web search is unavailable right now ({ctx.web_unavailable}). "
                         "Answer from the videos and say the web could not be checked."}
    # A separate call: built-in Google Search is not mixed with function calling.
    try:
        candidate = gemini_client.generate(
            f"Search the web and answer factually and concisely: {query}",
            tools=[{"google_search": {}}],
            model=gemini_client.AGENT_MODEL_NAME,
            fallback_model=gemini_client.MODEL_NAME,
            max_output_tokens=1024,
            temperature=0.2,
        )
    except (gemini_client.QuotaError, gemini_client.TransientError) as error:
        # Don't let the model burn more calls on a search that can't succeed.
        ctx.web_unavailable = "quota exceeded" if isinstance(error, gemini_client.QuotaError) else "service busy"
        return {"error": f"Web search failed: {error}"}
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    sources = []
    for chunk in chunks[:MAX_WEB_SOURCES]:
        web = chunk.get("web") or {}
        if web.get("uri"):
            title = web.get("title") or web["uri"]
            sources.append({"id": ctx.add_web_source(title, web["uri"]), "title": title})
    return {"answer": gemini_client.candidate_text(candidate), "sources": sources}


def _meeting(ctx: ToolContext, video: str) -> dict | None:
    target = ctx.resolve(video)
    return target if target and target.get("kind") == "meeting" else None


def list_tasks(ctx: ToolContext, video: str) -> dict:
    target = _meeting(ctx, video)
    if target is None:
        return {"error": f"'{video}' is not a meeting in this workspace."}
    return {"tasks": [
        {"task": t["text"], "owner": t["contact_name"] or t["owner_name"] or "Unassigned",
         "has_contact": bool(t["contact_id"]), "due": t["due"], "status": t["status"]}
        for t in db.list_tasks(target["id"])
    ]}


def draft_task_emails(ctx: ToolContext, video: str, people: list[str] | None = None) -> dict:
    target = _meeting(ctx, video)
    if target is None:
        return {"error": f"'{video}' is not a meeting in this workspace."}

    contact_ids = None
    if people:
        wanted = {p.strip().lower() for p in people if p.strip()}
        contact_ids = [
            c["id"] for c in db.list_contacts()
            if c["name"].lower() in wanted or c["name"].split()[0].lower() in wanted
        ]
        if not contact_ids:
            return {"error": "None of those people are in the contacts list."}

    result = draft_emails(target["id"], contact_ids)
    ctx.drafts.extend(result["drafts"])
    return {
        "drafted_for": [d["contact_name"] for d in result["drafts"]],
        "tasks_without_a_contact": result["unassigned"],
        "note": "Drafts are waiting for the user to review and send. Nothing was sent.",
    }


# ── Declarations ─────────────────────────────────────────────────────
def _fn(name, description, properties=None, required=None):
    decl = {"name": name, "description": description}
    if properties:
        decl["parameters"] = {"type": "OBJECT", "properties": properties,
                              "required": required or []}
    return decl


_VIDEO = {"type": "STRING", "description": "Video reference such as V1"}

DECLARATIONS = {
    "list_videos": _fn("list_videos", "List the videos and meetings in this workspace."),
    "get_summary": _fn("get_summary", "Get the stored summary (or meeting minutes) of one video.",
                       {"video": _VIDEO}, ["video"]),
    "search_videos": _fn(
        "search_videos",
        "Semantic search over the transcripts and summaries of workspace videos. "
        "Returns excerpts with citation ids like V1-3.",
        {"query": {"type": "STRING", "description": "What to look for"},
         "videos": {"type": "ARRAY", "items": {"type": "STRING"},
                    "description": "Limit to these videos, e.g. [\"V1\"]. Omit to search all."}},
        ["query"],
    ),
    "web_search": _fn(
        "web_search",
        "Search the web with Google for information the videos don't contain. "
        "Returns an answer and sources with citation ids like W1.",
        {"query": {"type": "STRING", "description": "Search query"}}, ["query"],
    ),
    "list_tasks": _fn("list_tasks", "List the tasks extracted from a meeting, with owners.",
                      {"video": _VIDEO}, ["video"]),
    "draft_task_emails": _fn(
        "draft_task_emails",
        "Prepare email drafts telling people about their tasks from a meeting. "
        "Does NOT send; the user reviews and sends from the app.",
        {"video": _VIDEO,
         "people": {"type": "ARRAY", "items": {"type": "STRING"},
                    "description": "Only these people (names). Omit for everyone with tasks."}},
        ["video"],
    ),
}

IMPLEMENTATIONS = {
    "list_videos": list_videos,
    "get_summary": get_summary,
    "search_videos": search_videos,
    "web_search": web_search,
    "list_tasks": list_tasks,
    "draft_task_emails": draft_task_emails,
}


def declarations_for(ctx: ToolContext) -> list[dict]:
    names = list(DECLARATIONS)
    if not ctx.allow_web:
        names.remove("web_search")
    if not any(v.get("kind") == "meeting" for v in ctx.videos):
        names.remove("list_tasks")
        names.remove("draft_task_emails")
    return [DECLARATIONS[name] for name in names]


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    impl = IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return impl(ctx, **(args or {}))
    except TypeError as error:
        return {"error": f"Bad arguments for {name}: {error}"}
    except Exception as error:  # a failing tool should not end the conversation
        return {"error": str(error) or error.__class__.__name__}
