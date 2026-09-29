"""The workspace agent: a Gemini function-calling loop over the tools."""

from __future__ import annotations

import os

from core import gemini_client
from core.agent.prompts import FINAL_NUDGE, SYSTEM_PROMPT, WEB_OFF, WEB_ON
from core.agent.tools import ToolContext, declarations_for, run_tool

MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "6"))
MAX_HISTORY_TURNS = 6
NO_ANSWER = "I couldn't find an answer to that in these videos."


def web_enabled() -> bool:
    return os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _history_contents(history) -> list[dict]:
    turns = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            turns.append({"role": "user" if role == "user" else "model",
                          "parts": [{"text": text[:4000]}]})
    return turns[-MAX_HISTORY_TURNS * 2:]


def _describe(videos: list[dict]) -> str:
    lines = []
    for number, video in enumerate(videos, start=1):
        kind = "meeting" if video.get("kind") == "meeting" else "video"
        searchable = "" if video.get("qa_ready") else " (not searchable)"
        lines.append(f"- V{number} ({kind}): {video.get('title') or video.get('source', '')}{searchable}")
    return "\n".join(lines) or "- (empty)"


def _summarize_result(name: str, args: dict, result: dict) -> str:
    """One line for the "How I answered" trace."""
    if "error" in result:
        return f"{result['error']}"
    if name == "search_videos":
        return f"{len(result.get('results', []))} excerpt(s) for \"{args.get('query', '')}\""
    if name == "web_search":
        return f"{len(result.get('sources', []))} web source(s) for \"{args.get('query', '')}\""
    if name == "get_summary":
        return f"Read the summary of {result.get('ref', args.get('video', ''))}"
    if name == "list_tasks":
        return f"{len(result.get('tasks', []))} task(s) in {args.get('video', '')}"
    if name == "draft_task_emails":
        people = ", ".join(result.get("drafted_for", [])) or "nobody"
        return f"Drafted emails for {people}"
    if name == "list_videos":
        return f"{len(result.get('videos', []))} video(s)"
    return "done"


def run_agent(videos: list[dict], message: str, history=None, allow_web: bool = True) -> dict:
    """Answer ``message`` about ``videos``.

    Returns ``{"answer", "sources", "steps", "drafts"}``. ``steps`` is the
    ordered list of tool calls, for the UI's trace.
    """
    ctx = ToolContext(videos=videos, allow_web=allow_web and web_enabled())
    system = SYSTEM_PROMPT.format(
        videos=_describe(videos), web_rule=WEB_ON if ctx.allow_web else WEB_OFF
    )
    tools = [{"functionDeclarations": declarations_for(ctx)}]
    contents = _history_contents(history) + [{"role": "user", "parts": [{"text": message}]}]
    steps: list[dict] = []
    answer = ""

    for _ in range(MAX_STEPS + 1):
        out_of_steps = len(steps) >= MAX_STEPS
        candidate = gemini_client.generate(
            contents,
            tools=None if out_of_steps else tools,
            system=system,
            model=gemini_client.AGENT_MODEL_NAME,
            fallback_model=gemini_client.MODEL_NAME,
            max_output_tokens=2048,
            temperature=0.3,
        )
        parts = (candidate.get("content") or {}).get("parts") or []
        calls = [part["functionCall"] for part in parts if "functionCall" in part]

        if not calls or out_of_steps:
            answer = gemini_client.candidate_text(candidate)
            break

        # Echo the model's turn back verbatim (it may carry thought signatures).
        contents.append({"role": "model", "parts": parts})
        responses = []
        for call in calls:
            name, args = call.get("name", ""), call.get("args") or {}
            result = run_tool(ctx, name, args)
            steps.append({"tool": name, "args": args, "summary": _summarize_result(name, args, result)})
            responses.append({"functionResponse": {"name": name, "response": {"result": result}}})
        contents.append({"role": "user", "parts": responses})

        if len(steps) >= MAX_STEPS:
            contents.append({"role": "user", "parts": [{"text": FINAL_NUDGE}]})

    return {
        "answer": answer or NO_ANSWER,
        "sources": ctx.sources,
        "steps": steps,
        "drafts": ctx.drafts,
    }
