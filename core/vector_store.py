"""Persistent Chroma index of analyzed videos, used for transcript Q&A.

Every analysis is stored in one collection, tagged with its ``doc_id`` (the job
id), so a search only ever sees chunks from the video being asked about.

Embeddings use Chroma's built-in ONNX build of ``all-MiniLM-L6-v2``. It runs
locally, needs no API key, and does not depend on PyTorch. The model (~80 MB)
downloads once on first use.
"""

from __future__ import annotations

import os
import threading

from langchain_text_splitters import RecursiveCharacterTextSplitter


CHROMA_DIR = os.getenv("CHROMA_DIR", os.path.join("data", "chroma"))
COLLECTION_NAME = "video_chunks"

# Transcript chunks are small so a hit points at one specific passage; the
# generated sections are already dense, so they split less often.
TRANSCRIPT_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=150)
SECTION_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)

# Order matters only for display: it is the order sections are listed in.
SECTIONS = ("summary", "action_items", "decisions", "questions", "transcript")

_collection = None
_lock = threading.Lock()


def _get_collection():
    """Open the collection lazily so importing this module stays cheap."""
    global _collection
    with _lock:
        if _collection is None:
            import chromadb
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            client = chromadb.PersistentClient(path=CHROMA_DIR)
            _collection = client.get_or_create_collection(
                COLLECTION_NAME,
                embedding_function=DefaultEmbeddingFunction(),
                configuration={"hnsw": {"space": "cosine"}},
            )
        return _collection


def build_chunks(sections: dict[str, str]) -> list[tuple[str, str, int]]:
    """Split each section into ``(section, text, index)`` chunks, skipping blanks."""
    chunks = []
    for section in SECTIONS:
        text = (sections.get(section) or "").strip()
        if not text:
            continue
        splitter = TRANSCRIPT_SPLITTER if section == "transcript" else SECTION_SPLITTER
        for index, piece in enumerate(splitter.split_text(text)):
            chunks.append((section, piece, index))
    return chunks


def index_video(doc_id: str, sections: dict[str, str], title: str = "") -> int:
    """(Re)build the index for one analysis and return the number of chunks.

    Args:
        doc_id: The job id the chunks belong to.
        sections: Text keyed by names in ``SECTIONS`` (missing keys are skipped).
        title: Video title, stored on each chunk for reference.
    """
    chunks = build_chunks(sections)
    if not chunks:
        return 0

    collection = _get_collection()
    collection.delete(where={"doc_id": doc_id})
    collection.add(
        ids=[f"{doc_id}:{section}:{index}" for section, _, index in chunks],
        documents=[text for _, text, _ in chunks],
        metadatas=[
            {"doc_id": doc_id, "section": section, "chunk": index, "title": title}
            for section, _, index in chunks
        ],
    )
    return len(chunks)


def search(doc_id: str, query: str, k: int = 6) -> list[dict]:
    """Return the ``k`` chunks of one analysis most similar to ``query``."""
    collection = _get_collection()
    result = collection.query(
        query_texts=[query],
        n_results=k,
        where={"doc_id": doc_id},
    )

    hits = []
    for text, meta, distance in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        hits.append({
            "text": text,
            "section": meta.get("section", ""),
            "chunk": meta.get("chunk", 0),
            "score": round(1 - distance, 3),
        })
    return hits


def has_index(doc_id: str) -> bool:
    result = _get_collection().get(where={"doc_id": doc_id}, limit=1, include=[])
    return bool(result["ids"])


def delete_index(doc_id: str) -> None:
    _get_collection().delete(where={"doc_id": doc_id})
