import json
import os

import requests
from dotenv import load_dotenv


load_dotenv()

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/"


def generate_text(prompt: str, max_output_tokens: int = 1024) -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is missing. Add it to your .env file.")

    response = requests.post(
        f"{API_URL}{MODEL_NAME}:generateContent",
        headers={"x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": max_output_tokens,
            },
        },
        timeout=120,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Gemini API rate limit reached. Wait a moment and run the analysis again."
        )

    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        message = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(f"Gemini API request failed: {message}") from error

    try:
        parts = response.json()["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("Gemini returned no generated text.") from error


def generate_json(prompt: str) -> dict:
    text = generate_text(prompt, max_output_tokens=1024)
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("Gemini returned an invalid extraction response.") from error
