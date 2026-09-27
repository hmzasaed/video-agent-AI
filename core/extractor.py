# Actionable items, decisions, questions
from core.gemini_client import generate_text


_SHARED_STYLE = (
    "Use markdown. Use emojis to mark each entry. Be thorough and specific — quote or "
    "paraphrase the relevant detail rather than writing a one-line label. "
)


def extract_action_items(text: str) -> str:
    return generate_text(
        "From the video content below, extract every action item, task, or "
        "recommended next step.\n\n"
        "For each one give: the task, who owns it (or 'Unassigned'), and any deadline "
        "or timeframe mentioned (or 'No deadline given'). "
        + _SHARED_STYLE
        + "If there are genuinely none, reply exactly: 'No actionable items found.'\n\n"
        "CONTENT:\n" + text,
        max_output_tokens=2048,
    )


def extract_questions(text: str) -> str:
    return generate_text(
        "From the video content below, extract every question that was raised, asked, "
        "or left open.\n\n"
        "For each one give the question and, if it was answered, a short summary of the "
        "answer. "
        + _SHARED_STYLE
        + "If there are genuinely none, reply exactly: 'No questions found.'\n\n"
        "CONTENT:\n" + text,
        max_output_tokens=2048,
    )


def extract_decisions(text: str) -> str:
    return generate_text(
        "From the video content below, extract every decision, conclusion, or "
        "position that was settled on.\n\n"
        "For each one give the decision and the reasoning or context behind it. "
        + _SHARED_STYLE
        + "If there are genuinely none, reply exactly: 'No decisions found.'\n\n"
        "CONTENT:\n" + text,
        max_output_tokens=2048,
    )


def extract_information(text: str) -> dict:
    if not text.strip():
        return {
            "action_items": "No actionable items found.",
            "decisions": "No decisions found.",
            "questions": "No questions found.",
        }

    return {
        "action_items": extract_action_items(text),
        "decisions": extract_decisions(text),
        "questions": extract_questions(text),
    }
