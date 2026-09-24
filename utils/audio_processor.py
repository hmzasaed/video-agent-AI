import yt_dlp
from pydub import AudioSegment
import os


DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_youtube_audio(url  :str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = os.path.splitext(ydl.prepare_filename(info))[0] + '.mp3'
        return filename


def convert_audio_to_wav(input_path: str) -> str:
    output_path = os.path.splitext(input_path)[0] + '_converted.wav'
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(1).set_frame_rate(16000)  # Convert to mono and set frame rate
    audio.export(output_path, format='wav')
    return output_path


def chunk_audio(wav_path: str, chunk_minutes: int = 10) -> list:
    audio = AudioSegment.from_wav(wav_path)
    chunk_length_ms = chunk_minutes * 60 * 1000  # Convert minutes to milliseconds
    chunks = []
    for i, start in enumerate(range(0, len(audio), chunk_length_ms)):
        chunk = audio[start:start + chunk_length_ms]

        chunk_path = f"{wav_path}_chunk_{i}.wav"
        chunk.export(chunk_path, format='wav')
        chunks.append(chunk_path)
    return chunks


def process_input(source: str) -> list:
    if source.startswith("http://") or source.startswith("https://"):
        print("Detected YouTube URL. Downloading audio...")
        wav_path = download_youtube_audio(source)
    else:
        print("Detected local audio file. Processing... To WAV format if necessary.")
        wav_path = convert_audio_to_wav(source)

    print("Chunking audio ...")
    chunks = chunk_audio(wav_path)
    print(f"Audio has been chunked into {len(chunks)} created.")
    return chunks
