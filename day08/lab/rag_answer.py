"""
rag_answer.py — Sprint 2 + Sprint 3: Retrieval & Grounded Answer
================================================================
Sprint 2 (60 phút): Baseline RAG
  - Dense retrieval từ ChromaDB
  - Grounded answer function với prompt ép citation
  - Trả lời được ít nhất 3 câu hỏi mẫu, output có source

Sprint 3 (60 phút): Tuning tối thiểu
  - Thêm hybrid retrieval (dense + sparse/BM25)
  - Hoặc thêm rerank (cross-encoder)
  - Hoặc thử query transformation (expansion, decomposition, HyDE)
  - Tạo bảng so sánh baseline vs variant

Definition of Done Sprint 2:
  ✓ rag_answer("SLA ticket P1?") trả về câu trả lời có citation
  ✓ rag_answer("Câu hỏi không có trong docs") trả về "Không đủ dữ liệu"

Definition of Done Sprint 3:
  ✓ Có ít nhất 1 variant (hybrid / rerank / query transform) chạy được
  ✓ Giải thích được tại sao chọn biến đó để tune
"""

import json
import os
import re
from typing import List, Dict, Any, Optional, Set
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CẤU HÌNH
# =============================================================================

TOP_K_SEARCH = 10    # Số chunk lấy từ vector store trước rerank (search rộng)
TOP_K_SELECT = 3     # Số chunk gửi vào prompt sau rerank/select (top-3 sweet spot)

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
ABSTAIN_MESSAGE_VI = "Không đủ thông tin trong tài liệu hiện có để trả lời chắc chắn."
ABSTAIN_MESSAGE_EN = "I do not have enough information in the retrieved documents to answer confidently."

DOMAIN_HINTS = [
    {
        "keywords": {"sla", "ticket", "incident", "severity", "resolution", "p1", "p2", "p3", "p4"},
        "sources": {"support/sla-p1-2026.pdf"},
    },
    {
        "keywords": {"access", "approval matrix", "cấp quyền", "level 3", "level 4", "okta", "it-access", "jira"},
        "sources": {"it/access-control-sop.md"},
    },
    {
        "keywords": {"refund", "hoàn tiền", "flash sale", "store credit", "license key", "subscription"},
        "sources": {"policy/refund-v4.pdf"},
    },
    {
        "keywords": {"remote", "nghỉ phép", "leave", "probation", "team lead", "hr portal"},
        "sources": {"hr/leave-policy-2026.pdf"},
    },
    {
        "keywords": {"helpdesk", "mật khẩu", "password", "vpn", "sso", "email", "laptop", "đăng nhập"},
        "sources": {"support/helpdesk-faq.md"},
    },
]


# =============================================================================
# RETRIEVAL — DENSE (Vector Search)
# =============================================================================

def retrieve_dense(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """
    Search ChromaDB using OpenAI embeddings.
    """
    import chromadb
    from index import get_embedding, CHROMA_DB_DIR

    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    try:
        collection = client.get_collection("rag_lab")
    except:
        return []

    query_embedding = get_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )

    chunks = []
    if results["documents"]:
        ids = results.get("ids", [[]])[0]
        for idx, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
            chunks.append({
                "id": ids[idx] if idx < len(ids) else None,
                "text": doc,
                "metadata": meta,
                "score": 1.0 - dist # Cosine similarity
            })
    return chunks


# =============================================================================
# RETRIEVAL — SPARSE / BM25 (Keyword Search)
# Dùng cho Sprint 3 Variant hoặc kết hợp Hybrid
# =============================================================================

def _tokenize_for_bm25(text: str) -> List[str]:
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def retrieve_sparse(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """
    Search using BM25.
    """
    import chromadb
    from rank_bm25 import BM25Okapi
    from index import CHROMA_DB_DIR
    
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    try:
        collection = client.get_collection("rag_lab")
        all_docs = collection.get(include=["documents", "metadatas"])
    except:
        return []

    if not all_docs["documents"]:
        return []

    corpus = all_docs["documents"]
    ids = all_docs.get("ids", [])
    tokenized_corpus = [_tokenize_for_bm25(doc) for doc in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    
    tokenized_query = _tokenize_for_bm25(query)
    scores = bm25.get_scores(tokenized_query)
    
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    
    chunks = []
    for idx in top_indices:
        if scores[idx] > 0:
            chunks.append({
                "id": ids[idx] if idx < len(ids) else None,
                "text": corpus[idx],
                "metadata": all_docs["metadatas"][idx],
                "score": float(scores[idx])
            })
    return chunks


# =============================================================================
# RETRIEVAL — HYBRID (Dense + Sparse với Reciprocal Rank Fusion)
# =============================================================================

def retrieve_hybrid(
    query: str,
    top_k: int = TOP_K_SEARCH,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Combine Dense and Sparse search results using Reciprocal Rank Fusion (RRF).
    """
    dense_results = retrieve_dense(query, top_k=top_k * 2)
    sparse_results = retrieve_sparse(query, top_k=top_k * 2)

    # RRF scoring
    rrf_scores = {}
    doc_map = {}

    for rank, res in enumerate(dense_results, 1):
        doc_text = res["text"]
        rrf_scores[doc_text] = rrf_scores.get(doc_text, 0) + dense_weight * (1 / (60 + rank))
        doc_map[doc_text] = res

    for rank, res in enumerate(sparse_results, 1):
        doc_text = res["text"]
        rrf_scores[doc_text] = rrf_scores.get(doc_text, 0) + sparse_weight * (1 / (60 + rank))
        if doc_text not in doc_map:
            doc_map[doc_text] = res

    # Sort and return top_k
    sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    
    final_results = []
    for text, score in sorted_docs:
        chunk = doc_map[text].copy()
        chunk["rrf_score"] = score
        final_results.append(chunk)

    return final_results


# =============================================================================
# RERANK (Sprint 3 alternative)
# Cross-encoder để chấm lại relevance sau search rộng
# =============================================================================

def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = TOP_K_SELECT,
) -> List[Dict[str, Any]]:
    """
    LLM-based Re-ranker for better precision.
    """
    if not candidates: return []
    
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    # Prompt để LLM chọn top_k
    chunks_text = "\n".join([
        f"[{i}] {c['metadata'].get('source', 'unknown')} | {c['metadata'].get('section', '')}\n{c['text']}"
        for i, c in enumerate(candidates, 1)
    ])
    prompt = f"""Given the query: "{query}"
Select the top {top_k} most relevant chunks from the list below that directly help answer the query.
Return ONLY a JSON object with this shape: {{"indices": [1, 3, 5]}}.

List:
{chunks_text}

JSON:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        payload = json.loads(content)
        indices = payload.get("indices", []) if isinstance(payload, dict) else []
        
        ranked_candidates = []
        for idx in indices:
            if 1 <= idx <= len(candidates):
                ranked_candidates.append(candidates[idx-1])
        
        return ranked_candidates if ranked_candidates else candidates[:top_k]
    except Exception as e:
        print(f"[Rerank Error] {e}")
        return candidates[:top_k]


# =============================================================================
# QUERY TRANSFORMATION (Sprint 3 alternative)
# =============================================================================

def transform_query(query: str, strategy: str = "expansion") -> List[str]:
    """
    Expand query with synonyms and Vietnamese variations.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    prompt = f"""Given the Vietnamese query: "{query}"
Generate 2-3 alternative variations (synonyms, acronyms, or common helpdesk terms) to improve retrieval.
Return ONLY a JSON array of strings.

Variations:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        variations = json.loads(response.choices[0].message.content)
        if isinstance(variations, dict): variations = variations.get("variations", [])
        return [query] + variations
    except:
        return [query]


# =============================================================================
# GENERATION — GROUNDED ANSWER FUNCTION
# =============================================================================

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_list_items(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []

    items = []
    for item in value:
        text = _normalize_text(str(item))
        if text:
            items.append(text)
    return _dedupe_preserve_order(items)


def _is_document_name_query(query: str) -> bool:
    lowered = query.lower()
    return any(marker in lowered for marker in [
        "tài liệu nào",
        "tài liệu gì",
        "document name",
        "which document",
        "document is",
    ])


def _is_special_case_query(query: str) -> bool:
    lowered = query.lower()
    return (
        "khác không" in lowered
        or "đặc biệt" in lowered
        or "special case" in lowered
        or "same process" in lowered
        or "different process" in lowered
    )


def _is_timing_query(query: str) -> bool:
    lowered = query.lower()
    return any(marker in lowered for marker in [
        "bao lâu",
        "mấy ngày",
        "bao nhiêu ngày",
        "bao nhiêu phút",
        "thời gian",
        "sla",
        "how long",
        "timeline",
    ])


def _contains_text(haystack: str, needle: str) -> bool:
    normalized_haystack = _normalize_text(haystack).lower()
    normalized_needle = _normalize_text(needle).lower()
    return bool(normalized_needle) and normalized_needle in normalized_haystack


def _detail_is_covered(existing_text: str, detail: str) -> bool:
    if _contains_text(existing_text, detail):
        return True

    existing_tokens = set(_tokenize_for_bm25(existing_text))
    detail_tokens = [
        token for token in _tokenize_for_bm25(detail)
        if len(token) > 2 or any(ch.isdigit() for ch in token)
    ]
    if not detail_tokens:
        return False

    covered = sum(1 for token in detail_tokens if token in existing_tokens)
    return covered / len(detail_tokens) >= 0.8


def _humanize_source_name(source: str) -> str:
    basename = source.split("/")[-1]
    stem = re.sub(r"\.[a-z0-9]+$", "", basename, flags=re.IGNORECASE)
    words = re.split(r"[-_]+", stem)
    acronym_map = {
        "sop": "SOP",
        "sla": "SLA",
        "faq": "FAQ",
        "hr": "HR",
        "it": "IT",
        "p1": "P1",
        "p2": "P2",
        "p3": "P3",
        "p4": "P4",
    }
    humanized = [acronym_map.get(word.lower(), word.capitalize()) for word in words if word]
    return " ".join(humanized).strip()


def _canonical_name_from_chunks(chunks: List[Dict[str, Any]]) -> str:
    for chunk in chunks:
        source = chunk.get("metadata", {}).get("source", "")
        if source:
            return _humanize_source_name(source)
    return ""


def _localize_special_case_text(query: str, text: str) -> str:
    text = _normalize_text(text)
    if not text:
        return ""
    if not _looks_like_vietnamese(query):
        return text

    replacements = [
        (
            r"^Retrieved context does not mention a special-case rule for (.+)$",
            r"Tài liệu đã truy xuất không đề cập đến quy định đặc biệt cho \1",
        ),
        (
            r"^The retrieved context does not mention a special-case rule for (.+)$",
            r"Tài liệu đã truy xuất không đề cập đến quy định đặc biệt cho \1",
        ),
        (
            r"^The standard refund process includes (.+)$",
            r"Quy trình hoàn tiền chuẩn bao gồm \1",
        ),
        (
            r"^The standard process includes (.+)$",
            r"Quy trình chuẩn bao gồm \1",
        ),
    ]

    localized = text
    for pattern, replacement in replacements:
        localized = re.sub(pattern, replacement, localized, flags=re.IGNORECASE)
    return localized


def _extract_timing_details_from_chunks(
    chunks: List[Dict[str, Any]],
    max_items: int = 2,
    priority_keywords: Optional[Set[str]] = None,
) -> List[str]:
    priority_keywords = {keyword.lower() for keyword in (priority_keywords or set())}
    time_pattern = re.compile(
        r"\b\d+(?:-\d+)?\s*(?:phút|giờ|ngày(?: làm việc)?|tuần|hours?|minutes?|days?)\b",
        flags=re.IGNORECASE,
    )

    candidates = []
    for chunk in chunks:
        for raw_line in chunk.get("text", "").splitlines():
            line = _normalize_text(raw_line)
            if not line or not time_pattern.search(line):
                continue

            lowered = line.lower()
            priority_score = sum(1 for keyword in priority_keywords if keyword in lowered)
            candidates.append((priority_score, line))

    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    ordered_lines = _dedupe_preserve_order([line for _, line in candidates])
    return ordered_lines[:max_items]


def _append_unique_detail(parts: List[str], detail: str, prefix: str = "") -> None:
    detail = _normalize_text(detail)
    if not detail:
        return

    combined = " ".join(parts)
    if _detail_is_covered(combined, detail):
        return

    text = f"{prefix}{detail}".strip()
    if text and not _detail_is_covered(combined, text):
        parts.append(text)


def _compose_structured_answer(query: str, payload: Dict[str, Any], chunks: List[Dict[str, Any]]) -> str:
    direct_answer = _localize_special_case_text(query, str(payload.get("direct_answer", "")))
    fallback_answer = _localize_special_case_text(query, str(payload.get("answer", "")))
    main_answer = direct_answer or fallback_answer

    conditions = _normalize_list_items(payload.get("conditions", []))
    exceptions = _normalize_list_items(payload.get("exceptions", []))
    timeline_details = _normalize_list_items(payload.get("timeline_details", []))
    alias_names = _normalize_list_items(payload.get("alias_names", []))

    canonical_name = _normalize_text(str(payload.get("canonical_name", "")))
    special_case_status = _localize_special_case_text(query, str(payload.get("special_case_status", "")))
    standard_rule = _localize_special_case_text(query, str(payload.get("standard_rule", "")))

    source_canonical_name = _canonical_name_from_chunks(chunks)
    if _is_document_name_query(query) and source_canonical_name:
        canonical_name = source_canonical_name

    parts: List[str] = []
    if _is_document_name_query(query) and canonical_name:
        if alias_names:
            alias_text = '", "'.join(alias_names)
            parts.append(f'Tài liệu hiện có tên là "{canonical_name}", trước đây có tên "{alias_text}".')
        else:
            parts.append(f'Tài liệu hiện có tên là "{canonical_name}".')
    elif main_answer:
        parts.append(main_answer)

    if _is_document_name_query(query):
        if not canonical_name and alias_names:
            alias_text = '", "'.join(alias_names)
            _append_unique_detail(parts, f'Tài liệu trước đây có tên "{alias_text}".')

    if _is_special_case_query(query):
        if special_case_status:
            if not parts:
                parts.append(special_case_status)
            else:
                _append_unique_detail(parts, special_case_status)
        if standard_rule:
            _append_unique_detail(parts, f"Quy trình chuẩn áp dụng: {standard_rule}")
        special_case_timeline = timeline_details or _extract_timing_details_from_chunks(
            chunks,
            max_items=2,
            priority_keywords={"xử lý", "review", "process", "resolution", "phê duyệt", "quy trình"},
        )
        for detail in special_case_timeline:
            _append_unique_detail(parts, detail)

    if _is_timing_query(query):
        for detail in timeline_details:
            _append_unique_detail(parts, detail)

    for detail in exceptions:
        _append_unique_detail(parts, detail)

    for detail in conditions:
        _append_unique_detail(parts, detail)

    return _normalize_text(" ".join(parts))


def _lexical_overlap(query: str, text: str) -> float:
    query_tokens = {token for token in _tokenize_for_bm25(query) if len(token) > 2}
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize_for_bm25(text))
    return len(query_tokens & text_tokens) / len(query_tokens)


def _doc_key(chunk: Dict[str, Any]) -> str:
    meta = chunk.get("metadata", {})
    source = meta.get("source", "unknown")
    section = meta.get("section", "General")
    snippet = _normalize_text(chunk.get("text", ""))[:160]
    return f"{source}::{section}::{snippet}"


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    deduped = []
    for item in items:
        clean_item = _normalize_text(item)
        if clean_item and clean_item not in seen:
            seen.add(clean_item)
            deduped.append(clean_item)
    return deduped


def _query_variants(
    query: str,
    use_query_transform: bool = False,
    query_transform_strategy: str = "expansion",
) -> List[str]:
    if not use_query_transform:
        return [query]
    return _dedupe_preserve_order(transform_query(query, strategy=query_transform_strategy))


def _infer_preferred_sources(query: str) -> Set[str]:
    lowered_query = query.lower()
    preferred_sources: Set[str] = set()
    for hint in DOMAIN_HINTS:
        if any(keyword in lowered_query for keyword in hint["keywords"]):
            preferred_sources.update(hint["sources"])
    return preferred_sources


def _extract_query_anchors(query: str) -> List[str]:
    anchors = re.findall(r"[A-Z]{2,}(?:[-_][A-Z0-9]+)+", query.upper())
    priority_markers = re.findall(r"\bP[1-4]\b", query.upper())
    return _dedupe_preserve_order(anchors + priority_markers)


def _has_anchor_support(query: str, candidates: List[Dict[str, Any]]) -> bool:
    hard_anchors = [
        anchor for anchor in _extract_query_anchors(query)
        if not re.fullmatch(r"P[1-4]", anchor)
    ]
    if not hard_anchors:
        return True

    haystack_parts = []
    for chunk in candidates:
        haystack_parts.extend([
            chunk.get("text", ""),
            chunk.get("metadata", {}).get("source", ""),
            chunk.get("metadata", {}).get("section", ""),
        ])
    haystack = "\n".join(haystack_parts)
    normalized_haystack = haystack.upper()
    return all(anchor in normalized_haystack for anchor in hard_anchors)


def _merge_candidates(candidate_groups: List[List[Dict[str, Any]]], top_k: int) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    for group in candidate_groups:
        for rank, candidate in enumerate(group, 1):
            key = _doc_key(candidate)
            score = candidate.get("rrf_score", candidate.get("score", 0.0))
            fusion_bonus = 1 / (50 + rank)
            existing = merged.get(key)

            if existing is None:
                merged[key] = {
                    **candidate,
                    "retrieval_score": score + fusion_bonus,
                }
                continue

            existing["retrieval_score"] = max(existing.get("retrieval_score", 0.0), score + fusion_bonus)
            if score > existing.get("score", 0.0):
                existing["score"] = score
            if candidate.get("rrf_score", 0.0) > existing.get("rrf_score", 0.0):
                existing["rrf_score"] = candidate["rrf_score"]

    merged_candidates = list(merged.values())
    merged_candidates.sort(key=lambda item: item.get("retrieval_score", item.get("rrf_score", item.get("score", 0.0))), reverse=True)
    return merged_candidates[:top_k]


def _retrieve_candidates(
    query: str,
    retrieval_mode: str,
    top_k_search: int,
    use_query_transform: bool = False,
    query_transform_strategy: str = "expansion",
) -> List[Dict[str, Any]]:
    queries = _query_variants(
        query,
        use_query_transform=use_query_transform,
        query_transform_strategy=query_transform_strategy,
    )
    candidate_groups = []

    for variant_query in queries:
        if retrieval_mode == "dense":
            candidate_groups.append(retrieve_dense(variant_query, top_k=top_k_search))
        elif retrieval_mode == "sparse":
            candidate_groups.append(retrieve_sparse(variant_query, top_k=top_k_search))
        elif retrieval_mode == "hybrid":
            candidate_groups.append(retrieve_hybrid(variant_query, top_k=top_k_search))
        else:
            raise ValueError(f"retrieval_mode không hợp lệ: {retrieval_mode}")

    candidates = _merge_candidates(candidate_groups, top_k=top_k_search * 2)
    preferred_sources = _infer_preferred_sources(query)
    if not preferred_sources:
        return candidates

    rescored = []
    for candidate in candidates:
        meta = candidate.get("metadata", {})
        source = meta.get("source", "")
        text = candidate.get("text", "")
        section = meta.get("section", "")
        base_score = candidate.get("retrieval_score", candidate.get("rrf_score", candidate.get("score", 0.0)))
        boost = 0.0
        lexical_overlap = _lexical_overlap(query, f"{text} {section} {source}")

        if source in preferred_sources:
            boost += 0.2
        if any(anchor in f"{text} {section} {source}".upper() for anchor in _extract_query_anchors(query)):
            boost += 0.15
        if "tài liệu nào" in query.lower() and source:
            boost += 0.05
        boost += lexical_overlap * 0.25

        rescored.append({
            **candidate,
            "selection_score": base_score + boost,
        })

    rescored.sort(key=lambda item: item.get("selection_score", 0.0), reverse=True)
    return rescored[:top_k_search * 2]


def _looks_like_vietnamese(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ["không", "là", "bao lâu", "có thể", "phải", "được", "quy trình"])


def _abstain_message(query: str) -> str:
    return ABSTAIN_MESSAGE_VI if _looks_like_vietnamese(query) else ABSTAIN_MESSAGE_EN


def _normalize_citation_indices(raw_citations: Any, max_index: int) -> List[int]:
    if not isinstance(raw_citations, list):
        return []

    citations = []
    for item in raw_citations:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= max_index and value not in citations:
            citations.append(value)
    return citations


def _finalize_answer(
    query: str,
    payload: Dict[str, Any],
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    answer = _compose_structured_answer(query=query, payload=payload, chunks=chunks)
    citations = _normalize_citation_indices(payload.get("citations", []), len(chunks))
    abstain = bool(payload.get("abstain"))

    if abstain or not answer:
        return {
            "answer": _abstain_message(query),
            "citations": [],
            "sources": [],
            "abstained": True,
        }

    if not citations:
        return {
            "answer": _abstain_message(query),
            "citations": [],
            "sources": [],
            "abstained": True,
        }

    citation_suffix = "".join(f"[{idx}]" for idx in citations)
    if not re.search(r"\[\d+\]", answer):
        answer = f"{answer} {citation_suffix}".strip()

    sources = []
    for idx in citations:
        source = chunks[idx - 1].get("metadata", {}).get("source", "unknown")
        if source not in sources:
            sources.append(source)

    return {
        "answer": answer,
        "citations": citations,
        "sources": sources,
        "abstained": False,
    }


def build_context_block(chunks: List[Dict[str, Any]]) -> str:
    """
    Đóng gói danh sách chunks thành context block để đưa vào prompt.

    Format: structured snippets với source, section, score (từ slide).
    Mỗi chunk có số thứ tự [1], [2], ... để model dễ trích dẫn.
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        source = meta.get("source", "unknown")
        section = meta.get("section", "")
        department = meta.get("department", "")
        effective_date = meta.get("effective_date", "")
        score = chunk.get("score", 0)
        text = chunk.get("text", "")

        header = f"[{i}] {source}"
        if section:
            header += f" | {section}"
        if department:
            header += f" | dept={department}"
        if effective_date:
            header += f" | effective_date={effective_date}"
        if score > 0:
            header += f" | score={score:.2f}"

        context_parts.append(f"{header}\n{text}")

    return "\n\n".join(context_parts)


def build_grounded_prompt(query: str, context_block: str) -> str:
    """
    Xây dựng grounded prompt theo 4 quy tắc từ slide:
    1. Evidence-only: Chỉ trả lời từ retrieved context
    2. Abstain: Thiếu context thì nói không đủ dữ liệu
    3. Citation: Gắn source/section khi có thể
    4. Short, clear, stable: Output ngắn, rõ, nhất quán

    TODO Sprint 2:
    Đây là prompt baseline. Trong Sprint 3, bạn có thể:
    - Thêm hướng dẫn về format output (JSON, bullet points)
    - Thêm ngôn ngữ phản hồi (tiếng Việt vs tiếng Anh)
    - Điều chỉnh tone phù hợp với use case (CS helpdesk, IT support)
    """
    prompt = f"""You are a careful RAG assistant.
Answer only from the retrieved context below.
If the context is insufficient, abstain instead of guessing.
Every non-abstaining answer must cite at least one snippet index from the context.
If a snippet states an explicit exception, that exception overrides any general rule.
If the question asks for a document name and the context mentions an old name, answer with the current canonical name from the source field and mention the old name only as an alias.
If the source field is a file path, prefer the human-readable document title from the snippet and use the file path only as supporting detail.
If the question asks whether a special case differs from the standard policy and no explicit special-case rule is found, do NOT claim a company-wide absence as a hard fact. Instead say that the retrieved context does not mention a special-case rule, then state the standard rule if it appears in the context.
If the answer depends on an approval condition, exception, or qualifier, include that qualifier explicitly.
For SLA or policy timing questions, include all directly relevant time targets stated in the snippets.
For document-name questions, preserve both the current canonical name and any old alias if both appear.
For policy exception questions, include the scope of the exception, not just a bare yes/no.
Do not merge unrelated snippets just because they share a keyword.
Keep the answer short, clear, and factual.
Respond in the same language as the question.

Return ONLY JSON with this schema:
{{
  "direct_answer": "<main answer sentence>",
  "answer": "<optional fallback final answer>",
  "conditions": ["<mandatory qualifier or approval condition>"],
  "exceptions": ["<explicit exception or exclusion>"],
  "timeline_details": ["<relevant time target or SLA detail>"],
  "canonical_name": "<current canonical name if relevant>",
  "alias_names": ["<old name or alias if relevant>"],
  "special_case_status": "<for special-case questions: what the retrieved context explicitly says or does not mention>",
  "standard_rule": "<standard rule that applies if relevant>",
  "citations": [1, 2],
  "abstain": false,
  "reason": "<one short sentence>"
}}

Field guidance:
- `direct_answer`: always fill for non-abstaining answers.
- `conditions`: only facts that must be true for the answer to hold.
- `exceptions`: only explicit exceptions/exclusions found in context.
- `timeline_details`: include every directly relevant timing target, not just the most convenient one.
- `canonical_name` and `alias_names`: fill when the question is about a document or policy name.
- `special_case_status`: for questions like "có khác không", say "retrieved context does not mention a special-case rule for X" if no explicit special-case rule is found.
- `standard_rule`: include the applicable standard policy detail, such as processing time, approval path, or default rule.

Question: {query}

Context:
{context_block}
"""
    return prompt


def call_llm(prompt: str) -> Dict[str, Any]:
    """
    Sử dụng OpenAI gpt-4o-mini.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0, 
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {
            "answer": "",
            "citations": [],
            "abstain": True,
            "reason": "Model returned invalid JSON.",
        }
    return payload if isinstance(payload, dict) else {"answer": "", "citations": [], "abstain": True}


def rag_answer(
    query: str,
    retrieval_mode: str = "dense",
    top_k_search: int = TOP_K_SEARCH,
    top_k_select: int = TOP_K_SELECT,
    use_rerank: bool = False,
    use_query_transform: bool = False,
    query_transform_strategy: str = "expansion",
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Pipeline RAG hoàn chỉnh: query → retrieve → (rerank) → generate.

    Args:
        query: Câu hỏi
        retrieval_mode: "dense" | "sparse" | "hybrid"
        top_k_search: Số chunk lấy từ vector store (search rộng)
        top_k_select: Số chunk đưa vào prompt (sau rerank/select)
        use_rerank: Có dùng cross-encoder rerank không
        verbose: In thêm thông tin debug

    Returns:
        Dict với:
          - "answer": câu trả lời grounded
          - "sources": list source names trích dẫn
          - "chunks_used": list chunks đã dùng
          - "query": query gốc
          - "config": cấu hình pipeline đã dùng

    TODO Sprint 2 — Implement pipeline cơ bản:
    1. Chọn retrieval function dựa theo retrieval_mode
    2. Gọi rerank() nếu use_rerank=True
    3. Truncate về top_k_select chunks
    4. Build context block và grounded prompt
    5. Gọi call_llm() để sinh câu trả lời
    6. Trả về kết quả kèm metadata

    TODO Sprint 3 — Thử các variant:
    - Variant A: đổi retrieval_mode="hybrid"
    - Variant B: bật use_rerank=True
    - Variant C: thêm query transformation trước khi retrieve
    """
    config = {
        "retrieval_mode": retrieval_mode,
        "top_k_search": top_k_search,
        "top_k_select": top_k_select,
        "use_rerank": use_rerank,
        "use_query_transform": use_query_transform,
        "query_transform_strategy": query_transform_strategy,
    }

    # --- Bước 1: Retrieve ---
    candidates = _retrieve_candidates(
        query=query,
        retrieval_mode=retrieval_mode,
        top_k_search=top_k_search,
        use_query_transform=use_query_transform,
        query_transform_strategy=query_transform_strategy,
    )

    if verbose:
        print(f"\n[RAG] Query: {query}")
        print(f"[RAG] Retrieved {len(candidates)} candidates (mode={retrieval_mode})")
        for i, c in enumerate(candidates[:3]):
            preview_score = c.get("selection_score", c.get("retrieval_score", c.get("rrf_score", c.get("score", 0.0))))
            print(f"  [{i+1}] score={preview_score:.3f} | {c['metadata'].get('source', '?')}")

    if not candidates or not _has_anchor_support(query, candidates[:top_k_search]):
        abstain_answer = _abstain_message(query)
        return {
            "query": query,
            "answer": abstain_answer,
            "sources": [],
            "citations": [],
            "abstained": True,
            "chunks_used": [],
            "config": config,
        }

    # --- Bước 2: Rerank (optional) ---
    if use_rerank:
        candidates = rerank(query, candidates, top_k=top_k_select)
    else:
        candidates = candidates[:top_k_select]

    if verbose:
        print(f"[RAG] After select: {len(candidates)} chunks")

    if not candidates:
        abstain_answer = _abstain_message(query)
        return {
            "query": query,
            "answer": abstain_answer,
            "sources": [],
            "citations": [],
            "abstained": True,
            "chunks_used": [],
            "config": config,
        }

    # --- Bước 3: Build context và prompt ---
    context_block = build_context_block(candidates)
    prompt = build_grounded_prompt(query, context_block)

    if verbose:
        print(f"\n[RAG] Prompt:\n{prompt[:500]}...\n")

    # --- Bước 4: Generate ---
    llm_payload = call_llm(prompt)
    finalized = _finalize_answer(query=query, payload=llm_payload, chunks=candidates)

    return {
        "query": query,
        "answer": finalized["answer"],
        "sources": finalized["sources"],
        "citations": finalized["citations"],
        "abstained": finalized["abstained"],
        "chunks_used": candidates,
        "config": config,
    }


# =============================================================================
# SPRINT 3: SO SÁNH BASELINE VS VARIANT
# =============================================================================

def compare_retrieval_strategies(query: str) -> None:
    """
    So sánh các retrieval strategies với cùng một query.

    TODO Sprint 3:
    Chạy hàm này để thấy sự khác biệt giữa dense, sparse, hybrid.
    Dùng để justify tại sao chọn variant đó cho Sprint 3.

    A/B Rule (từ slide): Chỉ đổi MỘT biến mỗi lần.
    """
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print('='*60)

    strategies = ["dense", "hybrid"]  # Thêm "sparse" sau khi implement

    for strategy in strategies:
        print(f"\n--- Strategy: {strategy} ---")
        try:
            result = rag_answer(query, retrieval_mode=strategy, verbose=False)
            print(f"Answer: {result['answer']}")
            print(f"Sources: {result['sources']}")
        except NotImplementedError as e:
            print(f"Chưa implement: {e}")
        except Exception as e:
            print(f"Lỗi: {e}")


# =============================================================================
# MAIN — Demo và Test
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Sprint 2 + 3: RAG Answer Pipeline")
    print("=" * 60)

    # Test queries từ data/test_questions.json
    test_queries = [
        "SLA xử lý ticket P1 là bao lâu?",
        "Khách hàng có thể yêu cầu hoàn tiền trong bao nhiêu ngày?",
        "Ai phải phê duyệt để cấp quyền Level 3?",
        "ERR-403-AUTH là lỗi gì?",  # Query không có trong docs → kiểm tra abstain
    ]

    print("\n--- Sprint 2: Test Baseline (Dense) ---")
    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            result = rag_answer(query, retrieval_mode="dense", verbose=True)
            print(f"Answer: {result['answer']}")
            print(f"Sources: {result['sources']}")
        except NotImplementedError:
            print("Chưa implement — hoàn thành TODO trong retrieve_dense() và call_llm() trước.")
        except Exception as e:
            print(f"Lỗi: {e}")

    # Uncomment sau khi Sprint 3 hoàn thành:
    # print("\n--- Sprint 3: So sánh strategies ---")
    # compare_retrieval_strategies("Approval Matrix để cấp quyền là tài liệu nào?")
    # compare_retrieval_strategies("ERR-403-AUTH")

    print("\n\nViệc cần làm Sprint 2:")
    print("  1. Implement retrieve_dense() — query ChromaDB")
    print("  2. Implement call_llm() — gọi OpenAI hoặc Gemini")
    print("  3. Chạy rag_answer() với 3+ test queries")
    print("  4. Verify: output có citation không? Câu không có docs → abstain không?")

    print("\nViệc cần làm Sprint 3:")
    print("  1. Chọn 1 trong 3 variants: hybrid, rerank, hoặc query transformation")
    print("  2. Implement variant đó")
    print("  3. Chạy compare_retrieval_strategies() để thấy sự khác biệt")
    print("  4. Ghi lý do chọn biến đó vào docs/tuning-log.md")
