import json
import os
import time

import requests
from dotenv import load_dotenv


load_dotenv()

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# The agent makes tool-use decisions, which a fuller model handles noticeably better.
AGENT_MODEL_NAME = os.getenv("GEMINI_AGENT_MODEL", "gemini-3.5-flash")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/"

MAX_ATTEMPTS = 3


def _retry_delay(response, attempt: int) -> float:
    """Honour Retry-After when present, otherwise back off exponentially."""
    header = response.headers.get("Retry-After", "")
    try:
        return min(float(header), 30.0)
    except ValueError:
        return 2.0 * (2 ** attempt)


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TransientError(RuntimeError):
    """A temporary Gemini failure (overload, rate limit, timeout)."""


class QuotaError(RuntimeError):
    """The key has no quota left for this request; retrying will not help."""


def _error_message(response) -> str:
    try:
        return response.json().get("error", {}).get("message", "") or response.text
    except ValueError:
        return response.text


def generate(contents, tools=None, system=None, model=None,
             max_output_tokens: int = 1024, temperature: float = 0.4,
             fallback_model=None) -> dict:
    """Call ``generateContent`` and return the first candidate as a dict.

    ``contents`` is either a prompt string or a list of Gemini ``Content``
    objects (``{"role": ..., "parts": [...]}``). The candidate is returned raw so
    callers can read ``functionCall`` parts and ``groundingMetadata``.

    Rate limits, temporary server errors (5xx), and timeouts are retried up to
    ``MAX_ATTEMPTS`` times. If they persist — or the model's quota is used up,
    since quotas are per model — and ``fallback_model`` is given, the request
    is tried once with that model.
    """
    try:
        return _generate(contents, tools, system, model, max_output_tokens, temperature)
    except (TransientError, QuotaError):
        if not fallback_model or fallback_model == (model or MODEL_NAME):
            raise
        print(f"[gemini] {model or MODEL_NAME} unavailable; falling back to {fallback_model}")
        return _generate(contents, tools, system, fallback_model, max_output_tokens, temperature)


def _generate(contents, tools, system, model, max_output_tokens, temperature) -> dict:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is missing. Add it to your .env file.")

    if isinstance(contents, str):
        contents = [{"role": "user", "parts": [{"text": contents}]}]

    body = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if tools:
        body["tools"] = tools
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    response = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.post(
                f"{API_URL}{model or MODEL_NAME}:generateContent",
                headers={"x-goog-api-key": api_key},
                json=body,
                timeout=120,
            )
        except (requests.Timeout, requests.ConnectionError):
            response = None
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2.0 * (2 ** attempt))
            continue
        if response.status_code not in RETRYABLE_STATUS:
            break
        if response.status_code == 429 and "billing" in _error_message(response).lower():
            # Exhausted plan quota, not a per-minute limit: fail fast.
            raise QuotaError(
                "Your Gemini API key has no quota left for this request. "
                "Check your plan and usage at https://ai.dev/rate-limit."
            )
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(_retry_delay(response, attempt))
    else:
        if response is None:
            raise TransientError("Gemini did not respond in time. Check your connection and try again.")
        if response.status_code == 429:
            raise TransientError(
                "Gemini API rate limit reached. Wait a moment and run the analysis again."
            )
        raise TransientError(
            "Gemini is temporarily overloaded. Wait a moment and try again."
        )

    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        raise RuntimeError(f"Gemini API request failed: {_error_message(response)}") from error

    try:
        return response.json()["candidates"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("Gemini returned no generated text.") from error


def candidate_text(candidate: dict) -> str:
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts).strip()


def generate_text(prompt: str, max_output_tokens: int = 1024) -> str:
    candidate = generate(prompt, max_output_tokens=max_output_tokens)
    if not (candidate.get("content") or {}).get("parts"):
        raise RuntimeError("Gemini returned no generated text.")
    return candidate_text(candidate)


def generate_json(prompt: str, max_output_tokens: int = 1024):
    text = generate_text(prompt, max_output_tokens=max_output_tokens)
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("Gemini returned an invalid extraction response.") from error
