import sys
sys.stdout.reconfigure(encoding='utf-8')
from utils.audio_processor import process_input
from core.transcriber import transcribe_all
from core.summarize import summarize_transcript
from core.extractor import extract_information

if len(sys.argv) > 1:
    source = sys.argv[1]
else:
    source = input("Enter the video link or local file path: ").strip()

def main():
    chunks = process_input(source)
    transcript = transcribe_all(chunks)

    print("\n" + "=" * 60)
    print("TRANSCRIPT")
    print("📜 TRANSCRIPT")
    print("=" * 60)
    print(transcript)

    try:
        summary = summarize_transcript(transcript)
        information = extract_information(summary)
    except RuntimeError as error:
        print("\n" + "=" * 60)
        print("ANALYSIS STOPPED")
        print("=" * 60)
        print(error)
        return

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("📝 SUMMARY")
    print("=" * 60)
    print(summary)

    print("\n" + "=" * 60)
    print("ACTION ITEMS")
    print("✅ ACTION ITEMS")
    print("=" * 60)
    print(information["action_items"])

    print("\n" + "=" * 60)
    print("DECISIONS")
    print("⚖️ DECISIONS")
    print("=" * 60)
    print(information["decisions"])

    print("\n" + "=" * 60)
    print("QUESTIONS")
    print("❓ QUESTIONS")
    print("=" * 60)
    print(information["questions"])

if __name__ == "__main__":
    main()
