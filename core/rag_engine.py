"""Question answering over an analyzed video (retrieval-augmented generation).

Retrieve the chunks of one video most relevant to a question, then have Gemini
answer from those chunks only, citing them by number.
"""

from __future__ import annotations

import os

from core.gemini_client import generate_text
from core.vector_store import search


TOP_K = int(os.getenv("RAG_TOP_K", "6"))

# Earlier turns included in the prompt so follow-ups like "why?" make sense.
MAX_HISTORY_TURNS = 4

SECTION_LABELS = {
    "summary": "Summary",
    "minutes": "Meeting minutes",
    "action_items": "Action items",
    "decisions": "Decisions",
    "questions": "Questions",
    "transcript": "Transcript",
}

NO_CONTEXT_ANSWER = (
    "I could not find anything in this video related to that question."
)


def _clean_history(history) -> list[dict]:
    """Keep only well-formed ``{"role", "content"}`` turns, newest last."""
    turns = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            turns.append({"role": role, "content": content[:2000]})
    return turns[-MAX_HISTORY_TURNS * 2:]


def _retrieval_query(question: str, history: list[dict]) -> str:
    """Fold the previous question in, so a bare follow-up still retrieves well."""
    previous = [turn["content"] for turn in history if turn["role"] == "user"]
    return f"{previous[-1]}\n{question}" if previous else question


def _build_prompt(question: str, hits: list[dict], history: list[dict]) -> str:
    context = "\n\n".join(
        f"[{number}] ({SECTION_LABELS.get(hit['section'], hit['section'])})\n{hit['text']}"
        for number, hit in enumerate(hits, start=1)
    )
    conversation = "\n".join(
        f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}"
        for turn in history
    )

    return (
        "You answer questions about a single video using only the numbered "
        "excerpts below, taken from its transcript and its generated summary.\n\n"
        "Rules:\n"
        "1. Use only the excerpts. If they do not contain the answer, say so plainly "
        "and do not guess.\n"
        "2. Cite the excerpts you used inline as [1], [2], and so on.\n"
        "3. Prefer transcript excerpts for exact wording, numbers, and names.\n"
        "4. Be direct and concise. Use markdown bullet points when listing several items.\n\n"
        "EXCERPTS:\n" + context + "\n\n"
        + ("CONVERSATION SO FAR:\n" + conversation + "\n\n" if conversation else "")
        + "QUESTION: " + question + "\n\nANSWER:"
    )


def answer_question(doc_id: str, question: str, history=None) -> dict:
    """Answer ``question`` about the analysis ``doc_id``.

    Returns:
        ``{"answer": str, "sources": [{"id", "section", "text", "score"}]}``,
        where each source ``id`` matches a ``[n]`` citation in the answer.
    """
    question = question.strip()
    turns = _clean_history(history)

    hits = search(doc_id, _retrieval_query(question, turns), k=TOP_K)
    if not hits:
        return {"answer": NO_CONTEXT_ANSWER, "sources": []}

    answer = generate_text(
        _build_prompt(question, hits, turns),
        max_output_tokens=1536,
    )

    return {
        "answer": answer or NO_CONTEXT_ANSWER,
        "sources": [
            {
                "id": number,
                "section": hit["section"],
                "text": hit["text"],
                "score": hit["score"],
            }
            for number, hit in enumerate(hits, start=1)
        ],
    }
