import os
import re
import json
import math
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KB_FILE = Path(__file__).parent / "knowledge_base.txt"
EMBED_CACHE_FILE = Path(__file__).parent / "kb_embeddings_cache.json"
OPENAI_EMBED_MODEL = "text-embedding-3-small"

# ── In-memory stores ───────────────────────────────────────────────────────────
_chunks: list[str] = []           # text chunks
_embeddings: list[list[float]] = []  # parallel list of embedding vectors


# ── Text chunking ──────────────────────────────────────────────────────────────
def _load_and_chunk(file_path: Path, max_chars: int = 600) -> list[str]:
    """
    Split the knowledge base into Q&A chunks.
    Each 'Q:' block becomes its own chunk (plus its 'A:' answer).
    Falls back to fixed-size chunking if structure is unrecognised.
    """
    text = file_path.read_text(encoding="utf-8")
    # Split on Q: lines
    qa_blocks = re.split(r"\nQ:", text)
    chunks = []
    for block in qa_blocks:
        block = block.strip()
        if not block:
            continue
        # Re-attach the 'Q:' prefix we split on (except the first segment which is the header)
        if not block.startswith("Q:") and not block.startswith("BOOKLEAF"):
            block = "Q: " + block
        # Break large blocks into sub-chunks at max_chars
        while len(block) > max_chars:
            split_at = block.rfind(" ", 0, max_chars)
            if split_at == -1:
                split_at = max_chars
            chunks.append(block[:split_at].strip())
            block = block[split_at:].strip()
        if block:
            chunks.append(block)
    return chunks


# ── Embedding helpers ──────────────────────────────────────────────────────────
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (no numpy required)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_openai_embeddings(texts):
    return None

def _get_openai_embeddings_disabled(texts):
    """Fetch embeddings from OpenAI. Returns None on failure."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=OPENAI_EMBED_MODEL,
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        print(f"[KB] OpenAI embedding failed: {e}")
        return None


def _save_cache(chunks: list[str], embeddings: list[list[float]]) -> None:
    try:
        with open(EMBED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"chunks": chunks, "embeddings": embeddings}, f)
    except Exception as e:
        print(f"[KB] Cache save failed: {e}")


def _load_cache() -> tuple[list[str], list[list[float]]] | None:
    if not EMBED_CACHE_FILE.exists():
        return None
    try:
        with open(EMBED_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["chunks"], data["embeddings"]
    except Exception:
        return None


# ── Initialise (called once at app startup) ────────────────────────────────────
def init_knowledge_base(force_reload: bool = False) -> dict:
    """
    Load chunks and their embeddings into memory.
    Uses a local cache to avoid re-embedding on every restart.
    """
    global _chunks, _embeddings

    if not KB_FILE.exists():
        print(f"[KB] knowledge_base.txt not found at {KB_FILE}")
        return {"status": "error", "reason": "file_not_found"}

    chunks = _load_and_chunk(KB_FILE)

    # Try to use cached embeddings
    if not force_reload:
        cached = _load_cache()
        if cached and cached[0] == chunks:
            _chunks, _embeddings = cached
            print(f"[KB] Loaded {len(_chunks)} chunks from cache.")
            return {"status": "cached", "chunks": len(_chunks)}

    # Generate fresh embeddings
    embeddings = _get_openai_embeddings(chunks)
    if embeddings:
        _chunks = chunks
        _embeddings = embeddings
        _save_cache(chunks, embeddings)
        print(f"[KB] Embedded {len(_chunks)} chunks via OpenAI.")
        return {"status": "embedded", "chunks": len(_chunks)}
    else:
        # Store chunks without embeddings — will fall back to keyword search
        _chunks = chunks
        _embeddings = []
        print(f"[KB] Loaded {len(_chunks)} chunks (no embeddings — keyword fallback active).")
        return {"status": "keyword_only", "chunks": len(_chunks)}


# ── Keyword fallback search ────────────────────────────────────────────────────
def _keyword_search(query: str) -> tuple[str, float]:
    """
    Simple term-overlap search when embeddings aren't available.
    Returns (best_chunk, confidence_score).
    """
    if not _chunks:
        return ("", 0.0)

    query_words = set(re.findall(r"\w+", query.lower()))
    best_score = 0.0
    best_chunk = ""
    for chunk in _chunks:
        chunk_words = set(re.findall(r"\w+", chunk.lower()))
        overlap = len(query_words & chunk_words)
        score = overlap / max(len(query_words), 1)
        if score > best_score:
            best_score = score
            best_chunk = chunk
    return (best_chunk, min(best_score * 0.85, 0.85))  # cap at 0.85 for keyword


# ── Main search function ───────────────────────────────────────────────────────
def search_knowledge_base(query: str, top_k: int = 1) -> dict:
    """
    Search the knowledge base for the most relevant chunk.

    Returns:
        {
            "answer": str,          # best matching chunk text
            "confidence": float,    # 0.0 – 1.0
            "method": str,          # "embedding" | "keyword" | "empty"
        }
    """
    if not _chunks:
        # Try lazy init
        init_knowledge_base()
    if not _chunks:
        return {"answer": "", "confidence": 0.0, "method": "empty"}

    # ── Embedding-based search ──
    if _embeddings:
        query_vec = _get_openai_embeddings([query])
        if query_vec:
            qv = query_vec[0]
            scores = [_cosine_similarity(qv, ev) for ev in _embeddings]
            best_idx = scores.index(max(scores))
            return {
                "answer": _chunks[best_idx],
                "confidence": round(scores[best_idx], 4),
                "method": "embedding",
            }

    # ── Keyword fallback ──
    answer, confidence = _keyword_search(query)
    return {"answer": answer, "confidence": confidence, "method": "keyword"}
