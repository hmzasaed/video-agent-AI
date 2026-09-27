"""Whisper-based transcription helpers for processed audio chunks."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable


class Cancelled(Exception):
    """Raised when a caller asks for an in-flight job to stop."""


def transcribe_all(
    chunks: Iterable[str | Path],
    model_name: str = "base",
    progress: Callable[[str, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Transcribe audio chunks and return their combined text.

    Args:
        chunks: Paths returned by ``utils.audio_processor.process_input``.
        model_name: A Whisper model size, for example ``tiny``, ``base``, or
            ``small``. Larger models are slower but generally more accurate.
        progress: Optional callback invoked as ``(message, done, total)`` before
            each chunk is transcribed.
        should_cancel: Optional predicate checked between chunks. Whisper cannot
            be interrupted mid-chunk, so cancellation takes effect at the next
            chunk boundary.

    Raises:
        RuntimeError: If ``openai-whisper`` is not installed.
        FileNotFoundError: If one of the supplied audio chunks is missing.
        Cancelled: If ``should_cancel`` returns True between chunks.
    """
    chunk_paths = [Path(chunk) for chunk in chunks]
    if not chunk_paths:
        return ""

    missing_paths = [str(path) for path in chunk_paths if not path.is_file()]
    if missing_paths:
        raise FileNotFoundError(
            "Audio chunk(s) not found: " + ", ".join(missing_paths)
        )

    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "Whisper is not installed. Activate the virtual environment and run "
            "'pip install -r Requirements.txt'."
        ) from exc

    total = len(chunk_paths)
    if progress:
        progress("Loading the Whisper model...", 0, total)

    model = whisper.load_model(model_name)
    transcripts: list[str] = []

    for index, chunk_path in enumerate(chunk_paths, start=1):
        if should_cancel and should_cancel():
            raise Cancelled("Transcription cancelled.")

        message = f"Transcribing chunk {index}/{total}"
        try:
            print(f"{message}: {chunk_path.name}")
        except UnicodeEncodeError:
            safe_name = chunk_path.name.encode("ascii", "replace").decode("ascii")
            print(f"{message}: {safe_name}")

        if progress:
            progress(f"{message}...", index - 1, total)

        result = model.transcribe(str(chunk_path))
        text = result.get("text", "").strip()
        if text:
            transcripts.append(text)

    if progress:
        progress("Transcription complete.", total, total)

    return "\n\n".join(transcripts)
