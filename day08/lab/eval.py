"""
eval.py — Sprint 4: Evaluation & Scorecard
==========================================
Mục tiêu Sprint 4 (60 phút):
  - Chạy 10 test questions qua pipeline
  - Chấm điểm theo 4 metrics: Faithfulness, Relevance, Context Recall, Completeness
  - So sánh baseline vs variant
  - Ghi kết quả ra scorecard

Definition of Done Sprint 4:
  ✓ Demo chạy end-to-end (index → retrieve → answer → score)
  ✓ Scorecard trước và sau tuning
  ✓ A/B comparison: baseline vs variant với giải thích vì sao variant tốt hơn

A/B Rule (từ slide):
  Chỉ đổi MỘT biến mỗi lần để biết điều gì thực sự tạo ra cải thiện.
  Đổi đồng thời chunking + hybrid + rerank + prompt = không biết biến nào có tác dụng.
"""

import json
import csv
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from rag_answer import rag_answer

# =============================================================================
# CẤU HÌNH
# =============================================================================

TEST_QUESTIONS_PATH = Path(__file__).parent / "data" / "test_questions.json"
RESULTS_DIR = Path(__file__).parent / "results"

# Cấu hình baseline (Sprint 2)
BASELINE_CONFIG = {
    "retrieval_mode": "dense",
    "top_k_search": 10,
    "top_k_select": 3,
    "use_rerank": False,
    "use_query_transform": False,
    "query_transform_strategy": "expansion",
    "label": "baseline_dense",
}

# Cấu hình variant (Sprint 3 — điều chỉnh theo lựa chọn của nhóm)
# Variant chỉ đổi MỘT biến so với baseline để A/B comparison có ý nghĩa.
VARIANT_CONFIG = {
    "retrieval_mode": "hybrid",
    "top_k_search": 10,
    "top_k_select": 3,
    "use_rerank": False,
    "use_query_transform": False,
    "query_transform_strategy": "expansion",
    "label": "variant_hybrid_only",
}


# =============================================================================
# SCORING FUNCTIONS
# 4 metrics từ slide: Faithfulness, Answer Relevance, Context Recall, Completeness
# =============================================================================

ABSTAIN_PATTERNS = [
    "không đủ thông tin",
    "không tìm thấy thông tin",
    "tôi không biết",
    "không thể trả lời chắc chắn",
    "not enough information",
    "i do not know",
    "i don't know",
]


def is_abstention(answer: str) -> bool:
    normalized = re.sub(r"\s+", " ", (answer or "").strip().lower())
    return any(pattern in normalized for pattern in ABSTAIN_PATTERNS)


def is_insufficient_context_case(expected_sources: List[str], expected_answer: str) -> bool:
    normalized_expected = (expected_answer or "").lower()
    return (
        not expected_sources
        or "không tìm thấy thông tin" in normalized_expected
        or "không đủ dữ liệu" in normalized_expected
        or "do not know" in normalized_expected
        or "insufficient" in normalized_expected
    )


def build_evaluator_context(chunks_used: List[Dict[str, Any]]) -> str:
    parts = []
    for idx, chunk in enumerate(chunks_used, 1):
        meta = chunk.get("metadata", {})
        header = (
            f"[{idx}] source={meta.get('source', 'unknown')} | "
            f"section={meta.get('section', 'General')} | "
            f"effective_date={meta.get('effective_date', 'unknown')}"
        )
        parts.append(f"{header}\n{chunk.get('text', '')}")
    return "\n\n".join(parts)


def normalize_fact_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if item is None:
            continue
        text = re.sub(r"\s+", " ", str(item)).strip()
        if text:
            normalized.append(text)
    return normalized


def serialize_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_serialized_list(value: Any) -> List[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = value
            else:
                return normalize_fact_list(parsed)
    return normalize_fact_list(value)


def summarize_evaluator_notes(row: Dict[str, Any]) -> str:
    completeness_missing = parse_serialized_list(row.get("completeness_missing_facts"))
    if completeness_missing:
        return f"Completeness missing: {', '.join(completeness_missing[:2])}"

    context_missing = parse_serialized_list(row.get("context_missing_facts"))
    if context_missing:
        return f"Recall missing: {', '.join(context_missing[:2])}"

    unsupported_claims = parse_serialized_list(row.get("faithfulness_unsupported_claims"))
    if unsupported_claims:
        return f"Unsupported claims: {', '.join(unsupported_claims[:2])}"

    contradicted_claims = parse_serialized_list(row.get("faithfulness_contradicted_claims"))
    if contradicted_claims:
        return f"Contradicted claims: {', '.join(contradicted_claims[:2])}"

    for key in ["completeness_notes", "context_recall_notes", "faithfulness_notes"]:
        text = str(row.get(key, "")).strip()
        if text:
            return text
    return ""


def score_faithfulness(
    query: str,
    answer: str,
    chunks_used: List[Dict[str, Any]],
    expected_sources: Optional[List[str]] = None,
    expected_answer: str = "",
) -> Dict[str, Any]:
    """
    LLM-as-Judge: Faithfulness (Groundedness).
    """
    expected_sources = expected_sources or []

    if is_abstention(answer):
        if is_insufficient_context_case(expected_sources, expected_answer):
            return {
                "score": 5,
                "reason": "Accepted abstention: the answer avoids unsupported claims when the corpus lacks sufficient evidence.",
            }
        return {
            "score": 2,
            "reason": "The answer abstains even though the benchmark expects enough evidence to answer from context.",
        }

    if not chunks_used:
        return {
            "score": 1,
            "reason": "The answer makes a claim without any retrieved evidence.",
        }

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    context = build_evaluator_context(chunks_used)
    prompt = f"""You are a strict evaluator for FAITHFULNESS (groundedness) in a RAG system.

Judge whether the generated answer is supported by the retrieved context.
Do NOT judge completeness unless missing detail causes the answer to make an unsupported or misleading claim.

Evaluation rules:
- Score 5 only if every material claim in the answer is directly supported by the retrieved context.
- If the answer states a stronger claim than the context supports, lower the score.
- If the answer uses an outdated alias or contradicts a canonical current name stated in the context, lower the score.
- Distinguish carefully between:
  - omission: the answer leaves out a supported detail but does not invent anything
  - unsupported claim: the answer states something not established by the context
  - contradiction: the answer conflicts with the context
- Negative or absence claims need extra care:
  - If the answer says there is "no special rule", "no exception", or "không có", that claim is faithful only when the retrieved context explicitly supports that conclusion, or when the answer is narrowly phrased as "the retrieved context does not mention X".
  - Do NOT treat silence in the context as full support for a definitive absence claim.
- Keep omissions separate from hallucinations. An incomplete but otherwise grounded answer can still score high.

Scoring rubric:
- 5 = All material claims are directly supported.
- 4 = Mostly supported, with one minor overreach or ambiguity.
- 3 = Some support exists, but at least one important claim is not fully supported.
- 2 = Multiple important claims are unsupported or one major claim is misleading.
- 1 = The answer is largely unsupported or contradicts the retrieved context.

Return ONLY JSON with this exact schema:
{{
  "score": <integer 1-5>,
  "reason": "<short explanation>",
  "supported_claims": ["claim 1"],
  "unsupported_claims": ["claim 2"],
  "contradicted_claims": ["claim 3"]
}}

User Query:
{query}

Retrieved Context:
{context}

Generated Answer:
{answer}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        payload = json.loads(response.choices[0].message.content)
        if not isinstance(payload, dict):
            raise ValueError("Faithfulness judge did not return a JSON object.")

        try:
            score = int(payload.get("score", 3))
        except (TypeError, ValueError):
            score = 3
        score = max(1, min(5, score))

        return {
            "score": score,
            "reason": str(payload.get("reason", "")).strip() or "No reason provided by faithfulness judge.",
            "supported_claims": normalize_fact_list(payload.get("supported_claims")),
            "unsupported_claims": normalize_fact_list(payload.get("unsupported_claims")),
            "contradicted_claims": normalize_fact_list(payload.get("contradicted_claims")),
        }
    except Exception as e:
        return {"score": 3, "reason": f"Error in LLM-as-Judge: {e}"}


def score_answer_relevance(
    query: str,
    answer: str,
    expected_sources: Optional[List[str]] = None,
    expected_answer: str = "",
) -> Dict[str, Any]:
    """
    LLM-as-Judge: Answer Relevance.
    """
    expected_sources = expected_sources or []

    if is_abstention(answer):
        if is_insufficient_context_case(expected_sources, expected_answer):
            return {
                "score": 5,
                "reason": "The abstention is relevant because the benchmark expects the system to admit insufficient evidence.",
            }
        return {
            "score": 1,
            "reason": "The answer abstains instead of addressing a query that should be answerable from the retrieved corpus.",
        }

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    prompt = f"""Rate the RELEVANCE of the answer to the query.
- Score 5: Answer directly and fully addresses the user's question.
- Score 1: Answer is irrelevant or ignores the core question.

Query: {query}
Answer: {answer}

Rate 1-5 and provide a brief reason.
Return ONLY JSON: {{"score": <int>, "reason": "<string>"}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        import json
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"score": 3, "reason": f"Error: {e}"}


def score_context_recall(
    query: str,
    chunks_used: List[Dict[str, Any]],
    expected_answer: str,
    expected_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Context Recall: Retriever có mang về đủ evidence cần thiết không?
    """
    expected_sources = expected_sources or []

    if is_insufficient_context_case(expected_sources, expected_answer):
        return {
            "score": None,
            "recall": None,
            "required_facts": [],
            "supported_facts": [],
            "missing_facts": [],
            "notes": "No expected evidence because this benchmark case expects abstention / insufficient context.",
        }

    if not chunks_used:
        return {
            "score": 1,
            "recall": 0.0,
            "required_facts": [],
            "supported_facts": [],
            "missing_facts": ["No retrieved evidence"],
            "notes": "Retriever returned no chunks for an answerable benchmark case.",
        }

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    context = build_evaluator_context(chunks_used)
    prompt = f"""You are a strict evaluator for CONTEXT RECALL in a RAG pipeline.

Your job is to evaluate retrieval quality only.
Do NOT judge the generated answer. Judge whether the retrieved context contains the evidence needed to answer the query fully.

Use these inputs:
1. User Query
2. Expected Answer Key
3. Expected Sources
4. Retrieved Context

Evaluation process:
- Break the expected answer into atomic required evidence facts.
- A fact is required if it is needed to answer the query fully.
- Mark a fact as supported only if the retrieved context explicitly contains that fact or a clearly equivalent statement.
- Required evidence often includes:
  - direct answer facts
  - numeric values, deadlines, SLAs, or time ranges
  - conditions, qualifiers, and approvals
  - exceptions or exclusions
  - canonical current names and alias relations
  - standard-rule details needed to answer a "special case vs standard rule" query
- Do NOT give full credit just because the right document was retrieved.
- If the retriever found the correct source but missed the chunk containing a required detail, that detail is still missing.
- Treat source names only as supporting signals, not as the metric itself.

Scoring rubric:
- 5 = Retrieved context contains every required evidence fact.
- 4 = Retrieved context contains almost all required evidence, missing only one minor detail.
- 3 = Retrieved context contains the core evidence but misses one important detail.
- 2 = Retrieved context contains only partial evidence and misses multiple important facts.
- 1 = Retrieved context misses the core evidence needed to answer the query.

Return ONLY JSON with this exact schema:
{{
  "score": <integer 1-5>,
  "reason": "<short explanation>",
  "required_facts": ["fact 1", "fact 2"],
  "supported_facts": ["fact 1"],
  "missing_facts": ["fact 2"],
  "coverage_ratio": <number between 0 and 1>
}}

User Query:
{query}

Expected Answer Key:
{expected_answer}

Expected Sources:
{expected_sources}

Retrieved Context:
{context}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        payload = json.loads(response.choices[0].message.content)
        if not isinstance(payload, dict):
            raise ValueError("Context recall judge did not return a JSON object.")

        try:
            score = int(payload.get("score", 3))
        except (TypeError, ValueError):
            score = 3
        score = max(1, min(5, score))

        try:
            coverage_ratio = float(payload.get("coverage_ratio", 0.0))
        except (TypeError, ValueError):
            coverage_ratio = 0.0
        coverage_ratio = max(0.0, min(1.0, coverage_ratio))

        required_facts = normalize_fact_list(payload.get("required_facts"))
        supported_facts = normalize_fact_list(payload.get("supported_facts"))
        missing_facts = normalize_fact_list(payload.get("missing_facts"))

        return {
            "score": score,
            "recall": coverage_ratio,
            "required_facts": required_facts,
            "supported_facts": supported_facts,
            "missing_facts": missing_facts,
            "notes": str(payload.get("reason", "")).strip() or "No reason provided by context recall judge.",
        }
    except Exception as e:
        return {
            "score": 3,
            "recall": None,
            "required_facts": [],
            "supported_facts": [],
            "missing_facts": [],
            "notes": f"Error in context recall judge: {e}",
        }


def score_completeness(
    query: str,
    answer: str,
    expected_answer: str,
    chunks_used: Optional[List[Dict[str, Any]]] = None,
    expected_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    LLM-as-Judge: Completeness vs Expected.

    Completeness is stricter than generic semantic similarity:
    the judge must verify whether the answer covers the required facts,
    qualifiers, exceptions, and timing details needed to fully answer the query.
    """
    chunks_used = chunks_used or []
    expected_sources = expected_sources or []

    if is_abstention(answer):
        if is_insufficient_context_case(expected_sources, expected_answer):
            score = 5 if "không đủ thông tin" in answer.lower() or "not enough information" in answer.lower() else 4
            return {
                "score": score,
                "reason": "The answer abstains appropriately for an insufficient-context case instead of hallucinating missing facts.",
            }
        return {
            "score": 1,
            "reason": "The answer abstains even though the reference answer is supported by the corpus.",
        }

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    context = build_evaluator_context(chunks_used) or "(no retrieved context provided)"
    prompt = f"""You are a strict evaluator for the COMPLETENESS of a RAG answer.

Your job is to judge whether the generated answer includes ALL information needed to fully answer the user's query.
Judge completeness only. Do not reward style, fluency, or generic correctness.

Use these inputs carefully:
1. User Query: what the user actually asked.
2. Retrieved Context: use this as a tie-breaker to check whether a detail in the expected answer is actually supported and relevant.
3. Expected Answer Key: the primary answer key.
4. Generated Answer: the answer to grade.

Evaluation process:
- Break the expected answer into atomic required facts.
- Mark a fact as required if it is needed to fully answer the query and is supported by the retrieved context.
- Required facts often include:
  - the direct answer
  - numeric values, deadlines, SLAs, or time ranges
  - conditions or eligibility qualifiers
  - exceptions, exclusions, or override rules
  - approvals or prerequisite steps
  - canonical current document names and alias relationships
  - for "special case vs standard rule" questions: both whether the special case exists and the applicable standard rule
- Do NOT require unrelated extra details that are not needed to answer the query.
- Treat parenthetical examples or illustrations as minor supporting detail unless:
  - the user explicitly asked for examples, or
  - the examples are needed to define the scope of an exception or category boundary.
- Missing any required fact means the score must be below 5.
- If the answer gets the main point right but omits an important qualifier, exception, canonical name, or time detail, score 2-4 depending on severity.
- If the answer only states an old alias when the expected answer requires the current canonical name, treat that as incomplete.
- If multiple timing targets jointly define the answer (for example SLA response time and resolution time), treat each as separately required.
- For "special case vs standard rule" questions, if the expected answer includes a concrete standard-process detail such as a timeframe, approval path, or mandatory step, treat that concrete standard-rule detail as required.
- If the generated answer adds unsupported extra detail, mention it briefly in the reason, but keep the score focused mainly on missing required facts.

Scoring rubric:
- 5 = Covers every required fact needed for a fully complete answer.
- 4 = Main answer is correct but misses only a minor supporting detail.
- 3 = Main answer is correct but misses one important qualifier, exception, timeline, or condition.
- 2 = Partially answers the question but misses multiple important required facts.
- 1 = Misses, contradicts, or fails to provide the core answer.

Return ONLY JSON with this exact schema:
{{
  "score": <integer 1-5>,
  "reason": "<short explanation>",
  "required_facts": ["fact 1", "fact 2"],
  "covered_facts": ["fact 1"],
  "missing_facts": ["fact 2"]
}}

User Query:
{query}

Retrieved Context:
{context}

Expected Answer Key:
{expected_answer}

Generated Answer:
{answer}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        import json
        payload = json.loads(response.choices[0].message.content)
        if not isinstance(payload, dict):
            raise ValueError("Completeness judge did not return a JSON object.")

        try:
            score = int(payload.get("score", 3))
        except (TypeError, ValueError):
            score = 3
        score = max(1, min(5, score))

        return {
            "score": score,
            "reason": str(payload.get("reason", "")).strip() or "No reason provided by completeness judge.",
            "required_facts": normalize_fact_list(payload.get("required_facts")),
            "covered_facts": normalize_fact_list(payload.get("covered_facts")),
            "missing_facts": normalize_fact_list(payload.get("missing_facts")),
        }
    except Exception as e:
        return {"score": 3, "reason": f"Error: {e}"}


# =============================================================================
# SCORECARD RUNNER
# =============================================================================

def run_scorecard(
    config: Dict[str, Any],
    test_questions: Optional[List[Dict]] = None,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """
    Chạy toàn bộ test questions qua pipeline và chấm điểm.

    Args:
        config: Pipeline config (retrieval_mode, top_k, use_rerank, ...)
        test_questions: List câu hỏi (load từ JSON nếu None)
        verbose: In kết quả từng câu

    Returns:
        List scorecard results, mỗi item là một row

    TODO Sprint 4:
    1. Load test_questions từ data/test_questions.json
    2. Với mỗi câu hỏi:
       a. Gọi rag_answer() với config tương ứng
       b. Chấm 4 metrics
       c. Lưu kết quả
    3. Tính average scores
    4. In bảng kết quả
    """
    if test_questions is None:
        with open(TEST_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            test_questions = json.load(f)

    results = []
    label = config.get("label", "unnamed")

    print(f"\n{'='*70}")
    print(f"Chạy scorecard: {label}")
    print(f"Config: {config}")
    print('='*70)

    for q in test_questions:
        question_id = q["id"]
        query = q["question"]
        expected_answer = q.get("expected_answer", "")
        expected_sources = q.get("expected_sources", [])
        category = q.get("category", "")

        if verbose:
            print(f"\n[{question_id}] {query}")

        # --- Gọi pipeline ---
        try:
            result = rag_answer(
                query=query,
                retrieval_mode=config.get("retrieval_mode", "dense"),
                top_k_search=config.get("top_k_search", 10),
                top_k_select=config.get("top_k_select", 3),
                use_rerank=config.get("use_rerank", False),
                use_query_transform=config.get("use_query_transform", False),
                query_transform_strategy=config.get("query_transform_strategy", "expansion"),
                verbose=False,
            )
            answer = result["answer"]
            chunks_used = result["chunks_used"]

        except NotImplementedError:
            answer = "PIPELINE_NOT_IMPLEMENTED"
            chunks_used = []
        except Exception as e:
            answer = f"ERROR: {e}"
            chunks_used = []

        # --- Chấm điểm ---
        faith = score_faithfulness(
            query,
            answer,
            chunks_used,
            expected_sources=expected_sources,
            expected_answer=expected_answer,
        )
        relevance = score_answer_relevance(query, answer, expected_sources=expected_sources, expected_answer=expected_answer)
        recall = score_context_recall(
            query,
            chunks_used,
            expected_answer,
            expected_sources=expected_sources,
        )
        complete = score_completeness(
            query,
            answer,
            expected_answer,
            chunks_used=chunks_used,
            expected_sources=expected_sources,
        )

        row = {
            "id": question_id,
            "category": category,
            "query": query,
            "answer": answer,
            "expected_answer": expected_answer,
            "faithfulness": faith.get("score"),
            "faithfulness_notes": faith.get("reason", faith.get("notes", "")),
            "faithfulness_supported_claims": serialize_field(faith.get("supported_claims", [])),
            "faithfulness_unsupported_claims": serialize_field(faith.get("unsupported_claims", [])),
            "faithfulness_contradicted_claims": serialize_field(faith.get("contradicted_claims", [])),
            "relevance": relevance.get("score"),
            "relevance_notes": relevance.get("reason", relevance.get("notes", "")),
            "context_recall": recall.get("score"),
            "context_recall_notes": recall.get("notes", ""),
            "context_recall_ratio": recall.get("recall"),
            "context_required_facts": serialize_field(recall.get("required_facts", [])),
            "context_supported_facts": serialize_field(recall.get("supported_facts", [])),
            "context_missing_facts": serialize_field(recall.get("missing_facts", [])),
            "completeness": complete.get("score"),
            "completeness_notes": complete.get("reason", complete.get("notes", "")),
            "completeness_required_facts": serialize_field(complete.get("required_facts", [])),
            "completeness_covered_facts": serialize_field(complete.get("covered_facts", [])),
            "completeness_missing_facts": serialize_field(complete.get("missing_facts", [])),
            "retrieved_sources": serialize_field([
                c.get("metadata", {}).get("source", "")
                for c in chunks_used
            ]),
            "config_label": label,
        }
        results.append(row)

        if verbose:
            print(f"  Answer: {answer[:100]}...")
            print(f"  Faithful: {faith['score']} | Relevant: {relevance['score']} | "
                  f"Recall: {recall['score']} | Complete: {complete['score']}")

    # Tính averages (bỏ qua None)
    for metric in ["faithfulness", "relevance", "context_recall", "completeness"]:
        scores = [r[metric] for r in results if r[metric] is not None]
        avg = sum(scores) / len(scores) if scores else None
        print(f"\nAverage {metric}: {avg:.2f}" if avg is not None else f"\nAverage {metric}: N/A (chưa chấm)")

    return results


# =============================================================================
# A/B COMPARISON
# =============================================================================

def compare_ab(
    baseline_results: List[Dict],
    variant_results: List[Dict],
    output_csv: Optional[str] = None,
) -> None:
    """
    So sánh baseline vs variant theo từng câu hỏi và tổng thể.

    TODO Sprint 4:
    Điền vào bảng sau để trình bày trong báo cáo:

    | Metric          | Baseline | Variant | Delta |
    |-----------------|----------|---------|-------|
    | Faithfulness    |   ?/5    |   ?/5   |  +/?  |
    | Answer Relevance|   ?/5    |   ?/5   |  +/?  |
    | Context Recall  |   ?/5    |   ?/5   |  +/?  |
    | Completeness    |   ?/5    |   ?/5   |  +/?  |

    Câu hỏi cần trả lời:
    - Variant tốt hơn baseline ở câu nào? Vì sao?
    - Biến nào (chunking / hybrid / rerank) đóng góp nhiều nhất?
    - Có câu nào variant lại kém hơn baseline không? Tại sao?
    """
    metrics = ["faithfulness", "relevance", "context_recall", "completeness"]

    print(f"\n{'='*70}")
    print("A/B Comparison: Baseline vs Variant")
    print('='*70)
    print(f"{'Metric':<20} {'Baseline':>10} {'Variant':>10} {'Delta':>8}")
    print("-" * 55)

    for metric in metrics:
        b_scores = [r[metric] for r in baseline_results if r[metric] is not None]
        v_scores = [r[metric] for r in variant_results if r[metric] is not None]

        b_avg = sum(b_scores) / len(b_scores) if b_scores else None
        v_avg = sum(v_scores) / len(v_scores) if v_scores else None
        delta = (v_avg - b_avg) if (b_avg is not None and v_avg is not None) else None

        b_str = f"{b_avg:.2f}" if b_avg is not None else "N/A"
        v_str = f"{v_avg:.2f}" if v_avg is not None else "N/A"
        d_str = f"{delta:+.2f}" if delta is not None else "N/A"

        print(f"{metric:<20} {b_str:>10} {v_str:>10} {d_str:>8}")

    # Per-question comparison
    print(f"\n{'Câu':<6} {'Baseline F/R/Rc/C':<22} {'Variant F/R/Rc/C':<22} {'Better?':<10}")
    print("-" * 65)

    b_by_id = {r["id"]: r for r in baseline_results}
    for v_row in variant_results:
        qid = v_row["id"]
        b_row = b_by_id.get(qid, {})

        b_scores_str = "/".join([
            str(b_row.get(m, "?")) for m in metrics
        ])
        v_scores_str = "/".join([
            str(v_row.get(m, "?")) for m in metrics
        ])

        # So sánh đơn giản
        b_total = sum(b_row.get(m, 0) or 0 for m in metrics)
        v_total = sum(v_row.get(m, 0) or 0 for m in metrics)
        better = "Variant" if v_total > b_total else ("Baseline" if b_total > v_total else "Tie")

        print(f"{qid:<6} {b_scores_str:<22} {v_scores_str:<22} {better:<10}")

    # Export to CSV
    if output_csv:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = RESULTS_DIR / output_csv
        combined = baseline_results + variant_results
        if combined:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=combined[0].keys())
                writer.writeheader()
                writer.writerows(combined)
            print(f"\nKết quả đã lưu vào: {csv_path}")


# =============================================================================
# REPORT GENERATOR
# =============================================================================

def generate_scorecard_summary(results: List[Dict], label: str) -> str:
    """
    Tạo báo cáo tóm tắt scorecard dạng markdown.

    TODO Sprint 4: Cập nhật template này theo kết quả thực tế của nhóm.
    """
    metrics = ["faithfulness", "relevance", "context_recall", "completeness"]
    averages = {}
    for metric in metrics:
        scores = [r[metric] for r in results if r[metric] is not None]
        averages[metric] = sum(scores) / len(scores) if scores else None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# Scorecard: {label}
Generated: {timestamp}

## Summary

| Metric | Average Score |
|--------|--------------|
"""
    for metric, avg in averages.items():
        avg_str = f"{avg:.2f}/5" if avg is not None else "N/A"
        md += f"| {metric.replace('_', ' ').title()} | {avg_str} |\n"

    md += "\n## Per-Question Results\n\n"
    md += "| ID | Category | Faithful | Relevant | Recall | Complete | Notes |\n"
    md += "|----|----------|----------|----------|--------|----------|-------|\n"

    for r in results:
        notes = summarize_evaluator_notes(r)
        md += (f"| {r['id']} | {r['category']} | {r.get('faithfulness', 'N/A')} | "
               f"{r.get('relevance', 'N/A')} | {r.get('context_recall', 'N/A')} | "
               f"{r.get('completeness', 'N/A')} | {notes[:80]} |\n")

    return md


# =============================================================================
# MAIN — Chạy evaluation
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Sprint 4: Evaluation & Scorecard")
    print("=" * 60)

    # Kiểm tra test questions
    print(f"\nLoading test questions từ: {TEST_QUESTIONS_PATH}")
    try:
        with open(TEST_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            test_questions = json.load(f)
        print(f"Tìm thấy {len(test_questions)} câu hỏi")

        # In preview
        for q in test_questions[:3]:
            print(f"  [{q['id']}] {q['question']} ({q['category']})")
        print("  ...")

    except FileNotFoundError:
        print("Không tìm thấy file test_questions.json!")
        test_questions = []

    # --- Chạy Baseline ---
    print("\n--- Chạy Baseline ---")
    print("Lưu ý: Cần hoàn thành Sprint 2 trước khi chạy scorecard!")
    try:
        baseline_results = run_scorecard(
            config=BASELINE_CONFIG,
            test_questions=test_questions,
            verbose=True,
        )

        # Save scorecard
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        baseline_md = generate_scorecard_summary(baseline_results, "baseline_dense")
        scorecard_path = RESULTS_DIR / "scorecard_baseline.md"
        scorecard_path.write_text(baseline_md, encoding="utf-8")
        print(f"\nScorecard lưu tại: {scorecard_path}")

    except NotImplementedError:
        print("Pipeline chưa implement. Hoàn thành Sprint 2 trước.")
        baseline_results = []

    # --- Chạy Variant ---
    print("\n--- Chạy Variant ---")
    variant_results = run_scorecard(
        config=VARIANT_CONFIG,
        test_questions=test_questions,
        verbose=True,
    )
    variant_md = generate_scorecard_summary(variant_results, VARIANT_CONFIG["label"])
    (RESULTS_DIR / "scorecard_variant.md").write_text(variant_md, encoding="utf-8")

    # --- A/B Comparison ---
    if baseline_results and variant_results:
        compare_ab(
            baseline_results,
            variant_results,
            output_csv="ab_comparison.csv"
        )

    print("\n\nViệc cần làm Sprint 4:")
    print("  1. Hoàn thành Sprint 2 + 3 trước")
    print("  2. Chấm điểm thủ công hoặc implement LLM-as-Judge trong score_* functions")
    print("  3. Chạy run_scorecard(BASELINE_CONFIG)")
    print("  4. Chạy run_scorecard(VARIANT_CONFIG)")
    print("  5. Gọi compare_ab() để thấy delta")
    print("  6. Cập nhật docs/tuning-log.md với kết quả và nhận xét")
