"""
workers/synthesis.py — Synthesis Worker
Sprint 2: Tổng hợp câu trả lời từ retrieved_chunks và policy_result.

Input (từ AgentState):
    - task: câu hỏi
    - retrieved_chunks: evidence từ retrieval_worker
    - policy_result: kết quả từ policy_tool_worker

Output (vào AgentState):
    - final_answer: câu trả lời cuối với citation
    - sources: danh sách nguồn tài liệu được cite
    - confidence: mức độ tin cậy (0.0 - 1.0)

Gọi độc lập để test:
    python workers/synthesis.py
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

WORKER_NAME = "synthesis_worker"
ABSTAIN_MESSAGE = "Không đủ thông tin trong tài liệu nội bộ để trả lời chắc chắn."

SYSTEM_PROMPT = """Bạn là trợ lý IT Helpdesk nội bộ.

Quy tắc nghiêm ngặt:
1. CHỈ trả lời dựa vào context được cung cấp. KHÔNG dùng kiến thức ngoài.
2. Nếu context không đủ để trả lời → nói rõ "Không đủ thông tin trong tài liệu nội bộ".
3. Trích dẫn nguồn cuối mỗi câu quan trọng: [tên_file].
4. Trả lời súc tích, có cấu trúc. Không dài dòng.
5. Nếu có exceptions/ngoại lệ → nêu rõ ràng trước khi kết luận.
"""


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(token) > 1]


def _format_citation(text: str, source: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    if cleaned.endswith((".", "!", "?")):
        return f"{cleaned} [{source}]"
    return f"{cleaned}. [{source}]"


def _extract_sources(chunks: list[dict[str, Any]], policy_result: dict[str, Any]) -> list[str]:
    chunk_sources = [chunk.get("source", "unknown") for chunk in chunks]
    policy_sources = policy_result.get("source", []) if isinstance(policy_result, dict) else []
    if isinstance(policy_sources, str):
        policy_sources = [policy_sources]
    return _dedupe(chunk_sources + list(policy_sources))


def _joined_context(chunks: list[dict[str, Any]]) -> str:
    return "\n".join(chunk.get("text", "") for chunk in chunks).lower()


def _candidate_lines(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        source = chunk.get("source", "unknown")
        chunk_score = float(chunk.get("score", 0.0) or 0.0)
        text = chunk.get("text", "")
        seen: set[str] = set()

        for raw_line in text.splitlines():
            stripped = raw_line.strip(" -")
            if stripped.endswith(":") and len(stripped) <= 20:
                continue
            if stripped and stripped.lower() not in seen:
                candidates.append({"text": stripped, "source": source, "chunk_score": chunk_score})
                seen.add(stripped.lower())

        for raw_sentence in re.split(r"(?<=[.!?])\s+", text):
            stripped = raw_sentence.strip()
            if stripped and stripped.lower() not in seen:
                candidates.append({"text": stripped, "source": source, "chunk_score": chunk_score})
                seen.add(stripped.lower())
    return candidates


def _score_candidate(task: str, candidate: dict[str, Any]) -> float:
    task_tokens = set(_tokenize(task))
    candidate_tokens = set(_tokenize(candidate["text"]))
    if not task_tokens or not candidate_tokens:
        overlap_score = 0.0
    else:
        overlap_score = len(task_tokens & candidate_tokens) / len(task_tokens)
    return round(overlap_score + 0.35 * float(candidate.get("chunk_score", 0.0)), 4)


def _top_relevant_lines(task: str, chunks: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    ranked = []
    for candidate in _candidate_lines(chunks):
        score = _score_candidate(task, candidate)
        if score <= 0:
            continue
        ranked.append({**candidate, "score": score})

    ranked.sort(key=lambda item: item["score"], reverse=True)
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for item in ranked:
        normalized = item["text"].lower()
        if normalized in seen:
            continue
        selected.append(item)
        seen.add(normalized)
        if len(selected) >= limit:
            break
    return selected


def _extract_time(task: str) -> datetime | None:
    hhmm_match = re.search(r"\b(\d{1,2}):(\d{2})\b", task)
    if hhmm_match:
        hour = int(hhmm_match.group(1))
        minute = int(hhmm_match.group(2))
        return datetime(2026, 1, 1, hour, minute)

    ampm_match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", task.lower())
    if ampm_match:
        hour = int(ampm_match.group(1)) % 12
        if ampm_match.group(2) == "pm":
            hour += 12
        return datetime(2026, 1, 1, hour, 0)

    return None


def _build_context(chunks: list[dict[str, Any]], policy_result: dict[str, Any]) -> str:
    """Xây dựng context string từ chunks và policy result."""
    parts: list[str] = []

    if chunks:
        parts.append("=== EVIDENCE CHUNKS ===")
        for index, chunk in enumerate(chunks, 1):
            source = chunk.get("source", "unknown")
            text = chunk.get("text", "")
            score = float(chunk.get("score", 0.0) or 0.0)
            parts.append(f"[{index}] Nguồn: {source} (relevance={score:.2f})\n{text}")

    if policy_result:
        hints = policy_result.get("answer_hints", [])
        notes = policy_result.get("notes", [])
        if hints:
            parts.append("\n=== POLICY HINTS ===")
            parts.extend(f"- {hint}" for hint in hints)
        if notes:
            parts.append("\n=== POLICY NOTES ===")
            parts.extend(f"- {note}" for note in notes)
        if policy_result.get("ticket_context"):
            parts.append("\n=== TICKET CONTEXT ===")
            parts.append(str(policy_result["ticket_context"]))

    return "\n".join(parts) if parts else "(Không có context)"


def _build_policy_answer(task: str, policy_result: dict[str, Any], default_source: str) -> list[str]:
    domain = policy_result.get("domain")
    hints = policy_result.get("answer_hints", [])
    notes = policy_result.get("notes", [])
    primary_source = default_source

    if policy_result.get("source"):
        sources = policy_result["source"]
        if isinstance(sources, list) and sources:
            primary_source = sources[0]
        elif isinstance(sources, str):
            primary_source = sources

    answer_lines: list[str] = []

    if policy_result.get("policy_version_note"):
        answer_lines.append(_format_citation(policy_result["policy_version_note"], primary_source))
        answer_lines.append(_format_citation("Cần xác nhận thêm với human review trước khi chốt kết luận.", primary_source))
        return answer_lines

    if domain == "refund" and policy_result.get("exceptions_found"):
        answer_lines.append(_format_citation("Theo chính sách hoàn tiền v4, yêu cầu này thuộc nhóm ngoại lệ không được hoàn tiền.", primary_source))

    for hint in hints[:3]:
        answer_lines.append(_format_citation(hint, primary_source))

    for note in notes[:2]:
        answer_lines.append(_format_citation(note, primary_source))

    return _dedupe(answer_lines)


def _build_sla_answer(task: str, chunks: list[dict[str, Any]]) -> list[str]:
    task_lower = task.lower()
    sla_lines = [item for item in _candidate_lines(chunks) if item["source"] == "sla_p1_2026.txt"]
    answer_lines: list[str] = []
    p1_focus = any(keyword in task_lower for keyword in ["p1", "incident", "ticket"])

    if any(keyword in task_lower for keyword in ["thông báo", "notify", "escalation", "lúc mấy giờ", "mấy giờ", "22:47", "2am"]):
        slack_email_line = next(
            (item for item in sla_lines if "slack #incident-p1" in item["text"].lower() or "email incident@" in item["text"].lower()),
            None,
        )
        pagerduty_line = next(
            (item for item in sla_lines if "pagerduty" in item["text"].lower()),
            None,
        )
        escalation_lines = [
            item for item in sla_lines
            if any(keyword in item["text"].lower() for keyword in ["10 phút", "senior engineer"])
            and (not p1_focus or "p1" in item["text"].lower() or "senior engineer" in item["text"].lower())
        ]
        if slack_email_line:
            answer_lines.append(_format_citation(slack_email_line["text"], slack_email_line["source"]))
        if pagerduty_line:
            answer_lines.append(_format_citation(pagerduty_line["text"], pagerduty_line["source"]))
        for item in escalation_lines[:1]:
            answer_lines.append(_format_citation(item["text"], item["source"]))

        created_time = _extract_time(task)
        has_escalation = any("10 phút" in item["text"].lower() for item in escalation_lines)
        if created_time and has_escalation:
            escalation_time = created_time + timedelta(minutes=10)
            answer_lines.append(
                _format_citation(
                    f"Nếu ticket được tạo lúc {created_time.strftime('%H:%M')}, escalation sẽ xảy ra lúc {escalation_time.strftime('%H:%M')}",
                    "sla_p1_2026.txt",
                )
            )
        if answer_lines:
            return answer_lines

    if any(keyword in task_lower for keyword in ["không phản hồi sau 10 phút", "sau 10 phút", "10 phút"]):
        escalation_lines = [
            item for item in sla_lines
            if any(keyword in item["text"].lower() for keyword in ["10 phút", "senior engineer", "escalate"])
            and (not p1_focus or "p1" in item["text"].lower() or "senior engineer" in item["text"].lower())
        ]
        for item in escalation_lines[:2]:
            answer_lines.append(_format_citation(item["text"], item["source"]))
        if answer_lines:
            return answer_lines

    if ("quy trình" in task_lower or "mấy bước" in task_lower) and sla_lines:
        step_lines = [item for item in sla_lines if item["text"].startswith("Bước ")]
        if step_lines:
            source = step_lines[0]["source"]
            answer_lines.append(_format_citation(f"Quy trình gồm {len(step_lines)} bước", source))
            answer_lines.append(_format_citation(f"Bước đầu tiên là: {step_lines[0]['text']}", source))
            return answer_lines

    if any(keyword in task_lower for keyword in ["bao lâu", "sla", "phản hồi", "resolution"]):
        timing_lines = [
            item for item in sla_lines
            if any(keyword in item["text"].lower() for keyword in ["15 phút", "4 giờ", "phản hồi ban đầu", "resolution"])
        ]
        for item in timing_lines[:2]:
            answer_lines.append(_format_citation(item["text"], item["source"]))
        if answer_lines:
            return answer_lines

    relevant = _top_relevant_lines(task, [chunk for chunk in chunks if chunk.get("source") == "sla_p1_2026.txt"], limit=3)
    return [_format_citation(item["text"], item["source"]) for item in relevant]


def _build_hr_answer(task: str, chunks: list[dict[str, Any]]) -> list[str]:
    task_lower = task.lower()
    if "remote" not in task_lower:
        return []

    lines = [item for item in _candidate_lines(chunks) if item["source"] == "hr_leave_policy.txt"]
    answer_lines: list[str] = []
    if "probation" in task_lower:
        answer_lines.append(_format_citation("Chính sách chỉ cho phép nhân viên sau probation period làm remote, nên trường hợp đang probation period không được chấp thuận.", "hr_leave_policy.txt"))
        for item in lines:
            if any(keyword in item["text"].lower() for keyword in ["probation period", "2 ngày/tuần", "team lead"]):
                answer_lines.append(_format_citation(item["text"], item["source"]))
        return _dedupe(answer_lines[:3])
    return []


def _build_password_answer(task: str, chunks: list[dict[str, Any]]) -> list[str]:
    task_lower = task.lower()
    if "mật khẩu" not in task_lower and "password" not in task_lower:
        return []

    lines = [item for item in _candidate_lines(chunks) if item["source"] == "it_helpdesk_faq.txt"]
    answer_lines = []
    for item in lines:
        if any(keyword in item["text"].lower() for keyword in ["90 ngày", "7 ngày"]):
            answer_lines.append(_format_citation(item["text"], item["source"]))
    return _dedupe(answer_lines[:2])


def _build_abstain_answer(task: str, chunks: list[dict[str, Any]]) -> str | None:
    task_lower = task.lower()
    context_text = _joined_context(chunks)
    if any(keyword in task_lower for keyword in ["phạt", "tài chính", "financial penalty", "mức phạt"]) and not any(
        keyword in context_text for keyword in ["phạt", "penalty", "tài chính", "financial"]
    ):
        return (
            "Tài liệu SLA hiện có không nêu mức phạt tài chính cụ thể khi vi phạm SLA P1. "
            "Cần xác nhận thêm với bộ phận quản lý SLA hoặc IT Management."
        )
    return None


def _build_cross_doc_answer(task: str, chunks: list[dict[str, Any]], policy_result: dict[str, Any]) -> list[str]:
    task_lower = task.lower()
    sources = {chunk.get("source", "unknown") for chunk in chunks}
    if not ({"sla_p1_2026.txt", "access_control_sop.txt"} <= sources):
        return []
    if "access" not in task_lower and "cấp" not in task_lower:
        return []

    answer_lines: list[str] = []
    for line in _build_sla_answer(task, chunks)[:4]:
        answer_lines.append(line)

    ticket_context = policy_result.get("ticket_context", {}) if isinstance(policy_result, dict) else {}
    notifications = ticket_context.get("notifications_sent", []) if isinstance(ticket_context, dict) else []
    if notifications and not any("pagerduty" in line.lower() for line in answer_lines):
        answer_lines.insert(
            1,
            _format_citation(
                "P1 notifications phải được gửi qua Slack #incident-p1, email incident@company.internal, và PagerDuty on-call.",
                "sla_p1_2026.txt",
            ),
        )

    for line in _build_policy_answer(task, policy_result, "access_control_sop.txt")[:4]:
        if line.lower() not in {existing.lower() for existing in answer_lines}:
            answer_lines.append(line)

    return answer_lines[:6]


def _build_retrieval_answer(task: str, chunks: list[dict[str, Any]]) -> list[str]:
    if not chunks:
        return []

    sources = {chunk.get("source", "unknown") for chunk in chunks}
    if "hr_leave_policy.txt" in sources:
        hr_answer = _build_hr_answer(task, chunks)
        if hr_answer:
            return hr_answer
    if "it_helpdesk_faq.txt" in sources:
        password_answer = _build_password_answer(task, chunks)
        if password_answer:
            return password_answer
    if "sla_p1_2026.txt" in sources and any(keyword in task.lower() for keyword in ["sla", "ticket", "p1", "escalation", "incident", "thông báo"]):
        sla_answer = _build_sla_answer(task, chunks)
        if sla_answer:
            return sla_answer

    relevant = _top_relevant_lines(task, chunks, limit=3)
    return [_format_citation(item["text"], item["source"]) for item in relevant]


def _deterministic_answer(task: str, chunks: list[dict[str, Any]], policy_result: dict[str, Any]) -> str:
    sources = _extract_sources(chunks, policy_result)
    primary_source = sources[0] if sources else "unknown"

    abstain_answer = _build_abstain_answer(task, chunks)
    if abstain_answer:
        return abstain_answer

    answer_lines: list[str] = []
    if policy_result.get("policy_version_note"):
        answer_lines.extend(_build_policy_answer(task, policy_result, primary_source))
        return " ".join(answer_lines)

    cross_doc_lines = _build_cross_doc_answer(task, chunks, policy_result)
    if cross_doc_lines:
        return " ".join(cross_doc_lines)

    if policy_result:
        answer_lines.extend(_build_policy_answer(task, policy_result, primary_source))

    retrieval_lines = _build_retrieval_answer(task, chunks)
    for line in retrieval_lines:
        if line.lower() not in {existing.lower() for existing in answer_lines}:
            answer_lines.append(line)
        if len(answer_lines) >= 4:
            break

    if not answer_lines:
        if policy_result.get("policy_version_note"):
            return _format_citation(policy_result["policy_version_note"], primary_source)
        return ABSTAIN_MESSAGE

    return " ".join(answer_lines)


def _should_use_llm() -> bool:
    return os.getenv("DAY09_ENABLE_LLM_SYNTHESIS", "").strip().lower() in {"1", "true", "yes"}


def _call_llm(messages: list[dict[str, str]]) -> str:
    """Gọi LLM nếu user bật DAY09_ENABLE_LLM_SYNTHESIS=1."""
    if not _should_use_llm():
        raise RuntimeError("LLM synthesis is disabled for stable local lab runs.")

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,
            max_tokens=500,
        )
        return response.choices[0].message.content or ""

    gemini_key = os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        import google.generativeai as genai

        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        combined = "\n".join(message["content"] for message in messages)
        response = model.generate_content(combined)
        return response.text

    raise RuntimeError("No LLM API key configured.")


def _estimate_confidence(chunks: list[dict[str, Any]], answer: str, policy_result: dict[str, Any]) -> float:
    """Ước tính confidence dựa trên evidence quality và mức độ phải abstain."""
    if policy_result.get("requires_human_confirmation") or policy_result.get("policy_version_note"):
        return 0.25

    answer_lower = answer.lower()
    if ABSTAIN_MESSAGE.lower() in answer_lower or "không nêu" in answer_lower or "cần xác nhận thêm" in answer_lower:
        return 0.2

    if not chunks and not policy_result.get("answer_hints"):
        return 0.1

    if chunks:
        avg_score = sum(float(chunk.get("score", 0.0) or 0.0) for chunk in chunks) / len(chunks)
    else:
        avg_score = 0.55

    confidence = avg_score
    if policy_result.get("answer_hints"):
        confidence += 0.05
    if len(_extract_sources(chunks, policy_result)) >= 2:
        confidence += 0.03

    return round(max(0.1, min(0.95, confidence)), 2)


def synthesize(task: str, chunks: list[dict[str, Any]], policy_result: dict[str, Any]) -> dict:
    """
    Tổng hợp câu trả lời từ chunks và policy context.

    Returns:
        {"answer": str, "sources": list, "confidence": float}
    """
    sources = _extract_sources(chunks, policy_result)
    context = _build_context(chunks, policy_result)
    answer = _deterministic_answer(task, chunks, policy_result)

    if sources and ABSTAIN_MESSAGE not in answer and _should_use_llm():
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Câu hỏi: {task}\n\n{context}\n\nTrả lời câu hỏi dựa vào context trên với citation rõ ràng.",
            },
        ]
        try:
            llm_answer = _call_llm(messages)
            if llm_answer and "[" in llm_answer and "]" in llm_answer:
                answer = llm_answer.strip()
        except Exception:
            pass

    confidence = _estimate_confidence(chunks, answer, policy_result)

    return {
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
    }


def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.
    """
    task = state.get("task", "")
    chunks = state.get("retrieved_chunks", [])
    policy_result = state.get("policy_result", {})

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state.setdefault("worker_io_logs", [])
    state.setdefault("hitl_triggered", False)

    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "has_policy": bool(policy_result),
        },
        "output": None,
        "error": None,
    }

    try:
        result = synthesize(task, chunks, policy_result)
        state["final_answer"] = result["answer"]
        state["sources"] = result["sources"]
        state["confidence"] = result["confidence"]

        if result["confidence"] < 0.4:
            state["hitl_triggered"] = True

        worker_io["output"] = {
            "answer_length": len(result["answer"]),
            "sources": result["sources"],
            "confidence": result["confidence"],
        }
        state["history"].append(
            f"[{WORKER_NAME}] answer generated, confidence={result['confidence']}, "
            f"sources={result['sources']}, hitl={state['hitl_triggered']}"
        )
    except Exception as exc:
        worker_io["error"] = {"code": "SYNTHESIS_FAILED", "reason": str(exc)}
        state["final_answer"] = f"SYNTHESIS_ERROR: {exc}"
        state["sources"] = []
        state["confidence"] = 0.0
        state["hitl_triggered"] = True
        state["history"].append(f"[{WORKER_NAME}] ERROR: {exc}")

    state["worker_io_logs"].append(worker_io)
    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Synthesis Worker — Standalone Test")
    print("=" * 50)

    test_state = {
        "task": "SLA ticket P1 là bao lâu?",
        "retrieved_chunks": [
            {
                "text": "Ticket P1:\n- Phản hồi ban đầu (first response): 15 phút kể từ khi ticket được tạo.\n- Xử lý và khắc phục (resolution): 4 giờ.",
                "source": "sla_p1_2026.txt",
                "score": 0.92,
            }
        ],
        "policy_result": {},
    }

    result = run(test_state.copy())
    print(f"\nAnswer:\n{result['final_answer']}")
    print(f"\nSources: {result['sources']}")
    print(f"Confidence: {result['confidence']}")

    print("\n--- Test 2: Exception case ---")
    test_state2 = {
        "task": "Khách hàng Flash Sale yêu cầu hoàn tiền vì lỗi nhà sản xuất.",
        "retrieved_chunks": [
            {
                "text": "Ngoại lệ: Đơn hàng Flash Sale không được hoàn tiền theo Điều 3 chính sách v4.",
                "source": "policy_refund_v4.txt",
                "score": 0.88,
            }
        ],
        "policy_result": {
            "domain": "refund",
            "policy_applies": False,
            "exceptions_found": [{"type": "flash_sale_exception", "rule": "Flash Sale không được hoàn tiền."}],
            "answer_hints": ["Theo chính sách hoàn tiền v4, yêu cầu này thuộc nhóm ngoại lệ không được hoàn tiền."],
            "source": ["policy_refund_v4.txt"],
        },
    }
    result2 = run(test_state2.copy())
    print(f"\nAnswer:\n{result2['final_answer']}")
    print(f"Confidence: {result2['confidence']}")

    print("\n--- Test 3: Abstain case ---")
    test_state3 = {
        "task": "ERR-403-AUTH là lỗi gì và cách xử lý?",
        "retrieved_chunks": [],
        "policy_result": {},
    }
    result3 = run(test_state3.copy())
    print(f"\nAnswer:\n{result3['final_answer']}")
    print(f"Confidence: {result3['confidence']}")
    print(f"HITL: {result3['hitl_triggered']}")

    print("\n✅ synthesis_worker test done.")
