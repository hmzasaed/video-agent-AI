"""Map-reduce summarization of a transcript.

The model is chosen by ``SUMMARY_PROVIDER``: ``gemini`` (default) or
``mistral`` (via LangChain, needs ``MISTRAL_API_KEY``). Both use the same
prompts, length banding, progress reporting, and cancellation.
"""

import os
import time
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.gemini_client import generate_text
from core.transcriber import Cancelled

MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")


def provider() -> str:
    return "mistral" if os.getenv("SUMMARY_PROVIDER", "gemini").strip().lower() == "mistral" else "gemini"


def _mistral(prompt: str, max_output_tokens: int) -> str:
    # Imported lazily so the Gemini path never needs langchain-mistralai.
    from langchain_mistralai import ChatMistralAI

    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key or api_key.startswith("your_"):
        raise RuntimeError("MISTRAL_API_KEY is missing. Add it to your .env file.")
    llm = ChatMistralAI(model=MISTRAL_MODEL, api_key=api_key,
                        temperature=0.3, max_tokens=max_output_tokens)
    return str(llm.invoke(prompt).content).strip()


def _llm(prompt: str, max_output_tokens: int) -> str:
    if provider() == "mistral":
        return _mistral(prompt, max_output_tokens)
    return generate_text(prompt, max_output_tokens=max_output_tokens)


def generate_title(transcript: str) -> str:
    """A concise title (at most 8 words) for a transcript."""
    return _llm(
        "Generate a concise, informative title for this transcript, at most 8 words. "
        "Return only the title, with no quotes or formatting.\n\n" + transcript[:2000],
        max_output_tokens=40,
    ).strip().strip('"')


def split_transcript(transcript: str) -> list:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=6000,
        chunk_overlap=400,
    )
    return text_splitter.split_text(transcript)


def summarize_transcript(transcript: str, progress=None, should_cancel=None) -> str:
    """Summarize a transcript in two passes: per-section notes, then expansion.

    Args:
        transcript: The full transcript text.
        progress: Optional callback receiving a status message.
        should_cancel: Optional predicate checked between sections.
    """
    if not transcript.strip():
        return ""

    chunks = split_transcript(transcript)
    chunk_summaries = []

    for index, chunk in enumerate(chunks, start=1):
        if should_cancel and should_cancel():
            raise Cancelled("Summarization cancelled.")

        message = f"Summarizing section {index}/{len(chunks)}..."
        print(message)
        if progress:
            progress(message)

        chunk_summaries.append(
            _llm(
                "You are summarizing one section of a longer video transcript.\n"
                "Write a thorough, detailed set of bullet points that preserves every "
                "topic, example, number, name, and conclusion mentioned. Do not "
                "compress aggressively — it is better to be long and complete than "
                "short. Do not add a title or any preamble.\n\n"
                "TRANSCRIPT SECTION:\n" + chunk,
                max_output_tokens=2048,
            )
        )
        if index < len(chunks):
            time.sleep(3)

    if progress:
        progress("Writing the final detailed summary...")

    return _llm(
        _final_prompt(transcript, chunk_summaries),
        max_output_tokens=8192,
    )


def _length_target(transcript: str) -> tuple[str, str]:
    """Pick a summary length that suits how much was actually said.

    A fixed "write at least 600 words" floor makes the model pad and invent
    detail when the source is a short clip, so the target scales with the
    transcript instead.
    """
    words = len(transcript.split())

    if words < 300:
        return (
            "This is a very short clip. Write a faithful summary of roughly "
            f"{max(60, words // 2)}-{max(120, words)} words",
            "Cover only what was actually said. Do not pad, speculate, or invent "
            "detail that is not in the notes — a short source deserves a short summary.",
        )
    if words < 1500:
        return (
            "Aim for roughly 300-500 words",
            "Be thorough but do not repeat yourself.",
        )
    return (
        "Aim for a long, rich write-up of at least 600 words",
        "Never respond with only a few short bullets.",
    )


def _final_prompt(transcript: str, chunk_summaries: list) -> str:
    length_rule, padding_rule = _length_target(transcript)

    return (
        "You are writing the final summary of a video, based on the section notes below.\n\n"
        "Requirements:\n"
        "1. Begin with a fitting title on its own line, formatted as: 📌 **<title>**\n"
        "2. Follow with a short overview paragraph of what the video covers.\n"
        "3. Then a section called 🗂️ **Detailed Breakdown** that walks through the "
        "content in order, using `##`-style headings for each major topic and bullet "
        "points underneath. Cover every topic from the notes — do not drop anything.\n"
        "4. Then a 💡 **Key Takeaways** section.\n"
        "5. Then a 🔑 **Important Details** section listing any names, numbers, tools, "
        "dates, or specific examples mentioned. Omit this section if there are none.\n\n"
        f"Length: {length_rule}. {padding_rule}\n"
        "Style: use markdown and emojis to mark important points.\n\n"
        "SECTION NOTES:\n" + "\n\n---\n\n".join(chunk_summaries)
    )
