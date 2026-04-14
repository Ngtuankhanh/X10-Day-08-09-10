"""
workers/retrieval.py — Retrieval Worker
Sprint 2: Implement retrieval từ ChromaDB, trả về chunks + sources.

Input (từ AgentState):
    - task: câu hỏi cần retrieve
    - top_k: số chunks cần lấy (default = 3)

Output (vào AgentState):
    - retrieved_chunks: list of {"text", "source", "score", "metadata"}
    - retrieved_sources: list of source filenames
    - worker_io_logs: log input/output của worker này

Gọi độc lập để test:
    python workers/retrieval.py
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

# ─────────────────────────────────────────────
# Worker Contract (xem contracts/worker_contracts.yaml)
# Input:  {"task": str, "top_k": int = 3}
# Output: {"retrieved_chunks": list, "retrieved_sources": list, "error": dict | None}
# ─────────────────────────────────────────────

WORKER_NAME = "retrieval_worker"
DEFAULT_TOP_K = 3
COLLECTION_NAME = "day09_docs"
LAB_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = LAB_DIR / "data" / "docs"
CHROMA_DB_DIR = LAB_DIR / "chroma_db"
MAX_CHUNK_CHARS = 900

SOURCE_NAME_MAP = {
    "refund-v4.pdf": "policy_refund_v4.txt",
    "sla-p1-2026.pdf": "sla_p1_2026.txt",
    "access-control-sop.md": "access_control_sop.txt",
    "helpdesk-faq.md": "it_helpdesk_faq.txt",
    "leave-policy-2026.pdf": "hr_leave_policy.txt",
}
STOPWORDS = {
    "la",
    "là",
    "gì",
    "gi",
    "và",
    "cho",
    "của",
    "cua",
    "thì",
    "thi",
    "được",
    "duoc",
    "không",
    "khong",
    "phải",
    "phai",
    "lỗi",
    "loi",
    "cách",
    "cach",
    "xử",
    "ly",
    "xửlý",
    "bao",
    "lâu",
    "lau",
    "nào",
    "nao",
    "nhiêu",
    "nhieu",
    "mấy",
    "may",
    "trong",
    "ngày",
    "ngay",
    "giờ",
    "gio",
    "vòng",
    "vong",
    "the",
    "nao",
    "cần",
    "can",
    "vì",
    "vi",
}
DOMAIN_SOURCE_HINTS = {
    "sla_p1_2026.txt": {"sla", "p1", "ticket", "incident", "escalation", "pagerduty", "on-call"},
    "policy_refund_v4.txt": {"refund", "hoàn", "hoan", "flash", "sale", "license", "subscription", "store", "credit"},
    "access_control_sop.txt": {"access", "cấp", "cap", "quyền", "quyen", "level", "admin", "security", "approver"},
    "it_helpdesk_faq.txt": {"password", "mật", "mat", "khẩu", "khau", "vpn", "sso", "email", "login", "đăng", "dang"},
    "hr_leave_policy.txt": {"remote", "probation", "hr", "nghỉ", "nghi", "phép", "phep", "leave"},
}


def _normalize_source(raw_source: str | None) -> str:
    """Chuẩn hóa source về filename trong lab."""
    if not raw_source:
        return "unknown"

    cleaned = raw_source.replace("\\", "/").strip()
    basename = Path(cleaned).name
    lower_basename = basename.lower()

    if lower_basename in SOURCE_NAME_MAP:
        return SOURCE_NAME_MAP[lower_basename]
    if lower_basename.endswith(".txt"):
        return basename

    normalized_stem = Path(lower_basename).stem.replace("-", "_")
    if normalized_stem:
        return f"{normalized_stem}.txt"
    return basename or "unknown"


def _tokenize(text: str) -> list[str]:
    tokens = []
    for token in re.findall(r"\w+", text.lower(), flags=re.UNICODE):
        if len(token) <= 1:
            continue
        if token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _split_large_block(text: str) -> list[str]:
    """Chia block dài thành các đoạn nhỏ hơn để retrieval ổn định."""
    normalized = text.strip()
    if not normalized:
        return []
    if len(normalized) <= MAX_CHUNK_CHARS:
        return [normalized]

    pieces: list[str] = []
    current = ""
    for paragraph in [p.strip() for p in normalized.split("\n\n") if p.strip()]:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= MAX_CHUNK_CHARS:
            current = candidate
            continue

        if current:
            pieces.append(current)
            current = ""

        if len(paragraph) <= MAX_CHUNK_CHARS:
            current = paragraph
            continue

        start = 0
        while start < len(paragraph):
            end = start + MAX_CHUNK_CHARS
            pieces.append(paragraph[start:end].strip())
            start = end

    if current:
        pieces.append(current)

    return [piece for piece in pieces if piece]


def _parse_document(doc_path: Path) -> list[dict[str, Any]]:
    """Đọc tài liệu .txt và tách thành chunks theo section."""
    raw_text = doc_path.read_text(encoding="utf-8")
    lines = raw_text.strip().splitlines()

    metadata: dict[str, Any] = {
        "source": doc_path.name,
        "doc_path": str(doc_path),
        "department": "unknown",
        "effective_date": "unknown",
        "access": "internal",
        "header_source": doc_path.name,
    }
    content_lines: list[str] = []
    header_done = False

    for line in lines:
        stripped = line.strip()
        if not header_done:
            if ":" in line and any(key in line for key in ["Source:", "Department:", "Effective Date:", "Access:"]):
                key, value = line.split(":", 1)
                normalized_key = key.strip().lower().replace(" ", "_")
                metadata[normalized_key] = value.strip()
                if normalized_key == "source":
                    metadata["header_source"] = value.strip()
                continue
            if stripped.startswith("==="):
                header_done = True
            elif not stripped:
                header_done = True
                continue
            elif stripped.isupper() and len(stripped) > 5:
                continue

        if header_done:
            content_lines.append(line)

    cleaned_text = "\n".join(content_lines).strip()
    if not cleaned_text:
        return []

    chunks: list[dict[str, Any]] = []
    current_section = "General"
    section_lines: list[str] = []
    chunk_index = 0

    def flush_section(section_name: str, body_lines: list[str], start_index: int) -> int:
        section_text = "\n".join(body_lines).strip()
        next_index = start_index
        if not section_text:
            return next_index

        sub_blocks: list[str] = []
        if "\nQ:" in f"\n{section_text}":
            qa_blocks = re.split(r"(?=^Q:\s)", section_text, flags=re.MULTILINE)
            sub_blocks = [block.strip() for block in qa_blocks if block.strip()]
        else:
            sub_blocks = _split_large_block(section_text)

        for block in sub_blocks:
            source = _normalize_source(metadata.get("source") or metadata.get("header_source"))
            chunk_metadata = dict(metadata)
            chunk_metadata["source"] = source
            chunk_metadata["section"] = section_name
            chunk_metadata["chunk_id"] = f"{doc_path.stem}_{next_index:03d}"
            chunks.append(
                {
                    "text": block,
                    "metadata": chunk_metadata,
                }
            )
            next_index += 1
        return next_index

    for line in cleaned_text.splitlines():
        stripped = line.strip()
        section_match = re.match(r"^===\s*(.*?)\s*===$", stripped)
        if section_match:
            chunk_index = flush_section(current_section, section_lines, chunk_index)
            current_section = section_match.group(1).strip()
            section_lines = []
            continue
        section_lines.append(line)

    flush_section(current_section, section_lines, chunk_index)
    return chunks


@lru_cache(maxsize=1)
def _load_bootstrap_chunks() -> tuple[dict[str, Any], ...]:
    """Load docs from repo for lexical fallback and local indexing bootstrap."""
    all_chunks: list[dict[str, Any]] = []
    for doc_path in sorted(DOCS_DIR.glob("*.txt")):
        all_chunks.extend(_parse_document(doc_path))
    return tuple(all_chunks)


def _source_boost(query_tokens: set[str], source: str) -> float:
    for hinted_source, keywords in DOMAIN_SOURCE_HINTS.items():
        if hinted_source != source:
            continue
        if query_tokens & keywords:
            return 0.35
    return 0.0


def _keyword_score(query: str, text: str, source: str) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0

    text_tokens = set(_tokenize(text))
    overlap = query_tokens & text_tokens
    if not overlap:
        return 0.0

    if len(overlap) == 1 and len(query_tokens) >= 3 and not _source_boost(query_tokens, source):
        return 0.0

    score = len(overlap) / len(query_tokens)
    if query.lower() in text.lower():
        score += 0.2
    score += _source_boost(query_tokens, source)
    return round(min(score, 1.0), 4)


def _retrieve_lexical(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """Fallback lexical retrieval khi ChromaDB hoặc embeddings chưa sẵn sàng."""
    scored_chunks: list[dict[str, Any]] = []
    for chunk in _load_bootstrap_chunks():
        source = _normalize_source(chunk["metadata"].get("source"))
        score = _keyword_score(query, chunk["text"], source)
        if score <= 0:
            continue

        metadata = dict(chunk["metadata"])
        metadata["source"] = source
        scored_chunks.append(
            {
                "text": chunk["text"],
                "source": source,
                "score": score,
                "metadata": metadata,
            }
        )

    scored_chunks.sort(key=lambda item: item["score"], reverse=True)
    return scored_chunks[:top_k]


@lru_cache(maxsize=1)
def _get_embedding_fn() -> Callable[[str], list[float]] | None:
    """
    Trả về embedding function.
    Ưu tiên Sentence Transformers (offline), fallback sang OpenAI nếu có key.
    """
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)

        def embed(text: str) -> list[float]:
            return model.encode([text])[0].tolist()

        return embed
    except Exception:
        pass

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)

            def embed(text: str) -> list[float]:
                response = client.embeddings.create(input=text, model="text-embedding-3-small")
                return response.data[0].embedding

            return embed
        except Exception:
            pass

    return None


def _bootstrap_collection(collection: Any) -> bool:
    """Populate local Chroma collection từ docs trong repo nếu collection đang trống."""
    embed = _get_embedding_fn()
    if embed is None:
        return False

    bootstrap_chunks = list(_load_bootstrap_chunks())
    if not bootstrap_chunks:
        return False

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []

    for index, chunk in enumerate(bootstrap_chunks):
        metadata = dict(chunk["metadata"])
        ids.append(metadata.get("chunk_id", f"day09_chunk_{index:03d}"))
        documents.append(chunk["text"])
        metadatas.append(metadata)
        embeddings.append(embed(chunk["text"]))

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return True


def _get_collection():
    """
    Kết nối ChromaDB collection.
    Tự bootstrap từ data/docs nếu collection chưa có dữ liệu.
    """
    try:
        import chromadb
    except Exception:
        return None

    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    try:
        if collection.count() == 0:
            _bootstrap_collection(collection)
    except Exception:
        return None

    return collection


def retrieve_dense(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """
    Dense retrieval: embed query → query ChromaDB → trả về top_k chunks.

    Returns:
        list of {"text": str, "source": str, "score": float, "metadata": dict}
    """
    collection = _get_collection()
    embed = _get_embedding_fn()

    if collection is None or embed is None:
        return _retrieve_lexical(query, top_k=top_k)

    try:
        query_embedding = embed(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "distances", "metadatas"],
        )

        documents = results.get("documents", [[]])
        distances = results.get("distances", [[]])
        metadatas = results.get("metadatas", [[]])
        if not documents or not documents[0]:
            return _retrieve_lexical(query, top_k=top_k)

        chunks: list[dict[str, Any]] = []
        for doc, dist, meta in zip(documents[0], distances[0], metadatas[0]):
            metadata = dict(meta or {})
            source = _normalize_source(metadata.get("source") or metadata.get("header_source"))
            metadata["source"] = source
            raw_score = 1.0 - float(dist or 1.0)
            score = round(max(0.0, min(1.0, raw_score)), 4)
            chunks.append(
                {
                    "text": doc,
                    "source": source,
                    "score": score,
                    "metadata": metadata,
                }
            )

        return chunks if chunks else _retrieve_lexical(query, top_k=top_k)
    except Exception:
        return _retrieve_lexical(query, top_k=top_k)


def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.

    Args:
        state: AgentState dict

    Returns:
        Updated AgentState với retrieved_chunks và retrieved_sources
    """
    task = state.get("task", "")
    top_k = state.get("top_k", state.get("retrieval_top_k", DEFAULT_TOP_K))

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state.setdefault("worker_io_logs", [])

    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {"task": task, "top_k": top_k},
        "output": None,
        "error": None,
    }

    try:
        chunks = retrieve_dense(task, top_k=top_k)
        sources = list(dict.fromkeys(chunk["source"] for chunk in chunks))

        state["retrieved_chunks"] = chunks
        state["retrieved_sources"] = sources

        worker_io["output"] = {
            "chunks_count": len(chunks),
            "sources": sources,
        }
        state["history"].append(
            f"[{WORKER_NAME}] retrieved {len(chunks)} chunks from {sources if sources else 'no sources'}"
        )
    except Exception as exc:
        worker_io["error"] = {"code": "RETRIEVAL_FAILED", "reason": str(exc)}
        state["retrieved_chunks"] = []
        state["retrieved_sources"] = []
        state["history"].append(f"[{WORKER_NAME}] ERROR: {exc}")

    state["worker_io_logs"].append(worker_io)
    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Retrieval Worker — Standalone Test")
    print("=" * 50)

    test_queries = [
        "SLA ticket P1 là bao lâu?",
        "Khách hàng Flash Sale yêu cầu hoàn tiền có được không?",
        "ERR-403-AUTH là lỗi gì?",
    ]

    for query in test_queries:
        print(f"\n▶ Query: {query}")
        result = run({"task": query})
        chunks = result.get("retrieved_chunks", [])
        print(f"  Retrieved: {len(chunks)} chunks")
        for chunk in chunks[:2]:
            print(f"    [{chunk['score']:.3f}] {chunk['source']}: {chunk['text'][:80]}...")
        print(f"  Sources: {result.get('retrieved_sources', [])}")

    print("\n✅ retrieval_worker test done.")
