"""Turn a YouTube URL or local media file into transcription-ready WAV chunks."""

import os

import yt_dlp
from pydub import AudioSegment


DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def _describe(info: dict) -> dict:
    """Pull the display fields we care about out of a yt-dlp info dict."""
    return {
        'title': info.get('title') or 'Untitled',
        'uploader': info.get('uploader') or info.get('channel') or '',
        'duration': info.get('duration') or 0,
        'thumbnail': info.get('thumbnail') or '',
        'webpage_url': info.get('webpage_url') or '',
    }


def download_youtube_audio(url: str, on_metadata=None) -> str:
    """Download the best audio track as MP3 and return its path.

    ``on_metadata`` is called with the video's title, uploader, duration, and
    thumbnail. The data comes from the download itself, so this costs no extra
    network request.
    """
    output_path = os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'restrictfilenames': True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if on_metadata:
            on_metadata(_describe(info))
        filename = os.path.splitext(ydl.prepare_filename(info))[0] + '.mp3'
        return filename


def convert_audio_to_wav(input_path: str) -> str:
    output_path = os.path.splitext(input_path)[0] + '_converted.wav'
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(1).set_frame_rate(16000)  # Convert to mono and set frame rate
    audio.export(output_path, format='wav')
    return output_path


def chunk_audio(audio_path: str, chunk_minutes: int = 10) -> list:
    """Split an audio file into transcription-ready WAV chunks."""
    audio = AudioSegment.from_file(audio_path)
    chunk_length_ms = chunk_minutes * 60 * 1000  # Convert minutes to milliseconds
    chunks = []
    for i, start in enumerate(range(0, len(audio), chunk_length_ms)):
        chunk = audio[start:start + chunk_length_ms]

        chunk_path = f"{audio_path}_chunk_{i}.wav"
        chunk.export(chunk_path, format='wav')
        chunks.append(chunk_path)
    return chunks


def process_input(source: str, on_metadata=None) -> list:
    """Return WAV chunk paths for a YouTube URL or a local media file.

    Args:
        source: A http(s) URL, or a path to a local audio/video file.
        on_metadata: Optional callback receiving a dict of display metadata.
            Only invoked for URLs; local files have no such metadata.
    """
    if source.startswith("http://") or source.startswith("https://"):
        print("Detected YouTube URL. Downloading audio...")
        mp3_path = download_youtube_audio(source, on_metadata=on_metadata)
        print("Converting downloaded audio to transcription-ready WAV...")
        wav_path = convert_audio_to_wav(mp3_path)
    else:
        print("Detected local audio file. Processing... To WAV format if necessary.")
        if not os.path.isfile(source):
            raise FileNotFoundError(f"No such file: {source}")
        if on_metadata:
            on_metadata({
                'title': os.path.basename(source),
                'uploader': '',
                'duration': 0,
                'thumbnail': '',
                'webpage_url': '',
            })
        wav_path = convert_audio_to_wav(source)

    print("Chunking audio ...")
    chunks = chunk_audio(wav_path)
    print(f"Audio has been chunked into {len(chunks)} created.")
    return chunks
