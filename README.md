# AI Video Analyzer Agent

An AI-assisted video analysis project that begins by downloading audio from a YouTube video and converting it to a transcription-ready WAV file. The broader goal is to build an agent that can transcribe, translate, analyze, and answer questions about video content.

## Current phase

**Phase 1 — Audio ingestion** is implemented in `utils/audio_processor.py`:

- Downloads the best available audio track from a YouTube URL using `yt-dlp`
- Extracts audio as an MP3
- Converts the audio to mono, 16 kHz WAV using `pydub` and FFmpeg
- Writes generated media to `downloads/` (ignored by Git)

## Planned phases

1. Audio ingestion and WAV conversion
2. Speech-to-text transcription with Whisper
3. Translation and text cleanup
4. Chunking, embeddings, and vector search
5. AI agent analysis and question answering
6. Streamlit user interface and exportable reports

## Setup

Prerequisites:

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) installed and available on your system `PATH`

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r Requirements.txt
```

Create a `.env` file from the example when later phases need the Mistral API:

```powershell
Copy-Item .env.example .env
```

Set `MISTRAL_API_KEY` in `.env`. Never commit this file.

## Run Phase 1

Update the YouTube URL in `utils/audio_processor.py`, then run:

```powershell
python utils/audio_processor.py
```

The MP3 and converted WAV will be saved in `downloads/`.

## Project structure

```text
utils/
  audio_processor.py    # Phase 1: download and normalize audio
downloads/              # Generated media (not tracked)
Requirements.txt        # Python dependencies
.env.example            # Safe environment-variable template
```

## Security

API keys and generated media are intentionally excluded from version control through `.gitignore`.
