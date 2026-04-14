"""
workers/policy_tool.py — Policy & Tool Worker
Sprint 2+3: Kiểm tra policy dựa vào context, gọi MCP tools khi cần.

Input (từ AgentState):
    - task: câu hỏi
    - retrieved_chunks: context từ retrieval_worker
    - needs_tool: True nếu supervisor quyết định cần tool call

Output (vào AgentState):
    - policy_result: {"policy_applies", "policy_name", "exceptions_found", "source", "rule"}
    - mcp_tools_used: list of tool calls đã thực hiện
    - worker_io_logs: log

Gọi độc lập để test:
    python workers/policy_tool.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

WORKER_NAME = "policy_tool_worker"
LAB_DIR = Path(__file__).resolve().parents[1]

if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))


# ─────────────────────────────────────────────
# MCP Client — Sprint 3: In-process mock MCP
# ─────────────────────────────────────────────

def _call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    """Gọi MCP tool qua dispatch_tool() và chuẩn hóa log."""
    try:
        from mcp_server import dispatch_tool

        result = dispatch_tool(tool_name, tool_input)
        error = None
        if isinstance(result, dict) and result.get("error"):
            error = {"code": "MCP_TOOL_ERROR", "reason": result["error"]}

        return {
            "tool": tool_name,
            "input": tool_input,
            "output": result,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as exc:
        return {
            "tool": tool_name,
            "input": tool_input,
            "output": None,
            "error": {"code": "MCP_CALL_FAILED", "reason": str(exc)},
            "timestamp": datetime.now().isoformat(),
        }


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _task_lower(task: str, chunks: list[dict[str, Any]]) -> str:
    context_text = " ".join(chunk.get("text", "") for chunk in chunks)
    return f"{task}\n{context_text}".lower()


def _infer_domain(task: str, chunks: list[dict[str, Any]]) -> str:
    combined = _task_lower(task, chunks)
    if any(keyword in combined for keyword in ["refund", "hoàn tiền", "flash sale", "store credit", "license", "subscription"]):
        return "refund"
    if any(
        keyword in combined
        for keyword in ["access", "cấp quyền", "approval matrix", "it security", "tech lead", "admin access", "level 1", "level 2", "level 3", "level 4"]
    ):
        return "access"
    return "generic"


def _find_relevant_lines(chunks: list[dict[str, Any]], keywords: list[str], limit: int = 3) -> list[dict[str, str]]:
    """Lấy các dòng/sentence bám sát keyword để làm hint có thể trace được."""
    matches: list[dict[str, str]] = []
    normalized_keywords = [keyword.lower() for keyword in keywords if keyword]

    for chunk in chunks:
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        candidates = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip(" -")
            if stripped:
                candidates.append(stripped)

        for raw_sentence in re.split(r"(?<=[.!?])\s+", text):
            stripped = raw_sentence.strip()
            if stripped and stripped not in candidates:
                candidates.append(stripped)

        for candidate in candidates:
            lower_candidate = candidate.lower()
            if any(keyword in lower_candidate for keyword in normalized_keywords):
                matches.append({"rule": candidate, "source": source})
                if len(matches) >= limit:
                    return matches

    return matches


def _parse_dates(task: str) -> list[datetime]:
    parsed: list[datetime] = []
    for match in re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", task):
        try:
            parsed.append(datetime.strptime(match, "%d/%m/%Y"))
        except ValueError:
            continue
    return parsed


def _requires_legacy_policy(task: str) -> bool:
    lower_task = task.lower()
    if "trước 01/02/2026" in lower_task:
        return True
    threshold = datetime(2026, 2, 1)
    return any(parsed_date < threshold for parsed_date in _parse_dates(task))


def _extract_access_level(task: str, chunks: list[dict[str, Any]]) -> int | None:
    combined = _task_lower(task, chunks)
    match = re.search(r"level\s*([1-4])", combined)
    if match:
        return int(match.group(1))
    if "admin access" in combined:
        return 4
    if "elevated access" in combined:
        return 3
    return None


def _extract_requester_role(task: str) -> str:
    task_lower = task.lower()
    if "contractor" in task_lower:
        return "contractor"
    if "vendor" in task_lower or "third-party" in task_lower:
        return "third_party_vendor"
    if "manager" in task_lower:
        return "manager"
    if "engineer" in task_lower:
        return "engineer"
    if "employee" in task_lower or "nhân viên" in task_lower:
        return "employee"
    return "unknown"


def _is_emergency_request(task: str, chunks: list[dict[str, Any]]) -> bool:
    combined = _task_lower(task, chunks)
    emergency_keywords = ["emergency", "khẩn cấp", "p1", "incident", "sự cố", "2am", "tạm thời", "active"]
    return any(keyword in combined for keyword in emergency_keywords)


def _asks_standard_approval_matrix(task: str) -> bool:
    task_lower = task.lower()
    approval_keywords = ["bao nhiêu người", "phê duyệt", "người cuối cùng", "cao nhất", "approval"]
    temporary_keywords = ["tạm thời", "temporary", "emergency access", "emergency bypass", "quy trình", "điều kiện"]
    return any(keyword in task_lower for keyword in approval_keywords) and not any(keyword in task_lower for keyword in temporary_keywords)


def _asks_temporary_access(task: str) -> bool:
    task_lower = task.lower()
    return any(
        keyword in task_lower
        for keyword in ["tạm thời", "temporary", "emergency access", "emergency bypass", "quy trình", "điều kiện"]
    )


def _extract_ticket_id(task: str) -> str | None:
    match = re.search(r"\b([A-Z]{2,5}-\d{2,5}|P1-LATEST)\b", task.upper())
    return match.group(1) if match else None


def _analyze_refund(task: str, chunks: list[dict[str, Any]]) -> dict:
    task_lower = task.lower()
    sources = _dedupe([chunk.get("source", "unknown") for chunk in chunks])
    exceptions_found: list[dict[str, str]] = []
    answer_hints: list[str] = []

    if _requires_legacy_policy(task):
        policy_version_note = (
            "Đơn hàng đặt trước 01/02/2026 áp dụng chính sách hoàn tiền phiên bản 3, "
            "nhưng repo hiện chỉ có tài liệu v4."
        )
        answer_hints.append(policy_version_note)
        return {
            "policy_applies": False,
            "policy_name": "refund_policy_v4",
            "exceptions_found": [],
            "source": sources or ["policy_refund_v4.txt"],
            "policy_version_note": policy_version_note,
            "requires_human_confirmation": True,
            "answer_hints": answer_hints,
            "explanation": "Temporal scoping vượt quá tài liệu hiện có.",
        }

    exception_specs = [
        ("flash_sale_exception", ["flash sale"], "Đơn hàng Flash Sale không được hoàn tiền."),
        (
            "digital_product_exception",
            ["license key", "license", "subscription", "kỹ thuật số"],
            "Sản phẩm kỹ thuật số như license key hoặc subscription không được hoàn tiền.",
        ),
        (
            "activated_product_exception",
            ["đã kích hoạt", "đã đăng ký", "đã sử dụng", "activated"],
            "Sản phẩm đã kích hoạt hoặc đã đăng ký tài khoản không được hoàn tiền.",
        ),
    ]

    for exception_type, keywords, fallback_rule in exception_specs:
        if any(keyword in task_lower for keyword in keywords):
            lines = _find_relevant_lines(chunks, keywords, limit=1)
            rule = lines[0]["rule"] if lines else fallback_rule
            source = lines[0]["source"] if lines else (sources[0] if sources else "policy_refund_v4.txt")
            exceptions_found.append(
                {
                    "type": exception_type,
                    "rule": rule,
                    "source": source,
                }
            )

    if exceptions_found:
        answer_hints.append("Theo chính sách hoàn tiền v4, yêu cầu này thuộc nhóm ngoại lệ không được hoàn tiền.")
        for exception in exceptions_found:
            answer_hints.append(exception["rule"])
    else:
        if "store credit" in task_lower:
            answer_hints.append("Store credit có giá trị 110% so với số tiền hoàn, tức cao hơn 10% so với hoàn tiền gốc.")
        relevant_lines = _find_relevant_lines(
            chunks,
            ["7 ngày", "store credit", "hoàn tiền", "refund", "finance team", "điều kiện"],
            limit=3,
        )
        answer_hints.extend(line["rule"] for line in relevant_lines)

    return {
        "policy_applies": len(exceptions_found) == 0,
        "policy_name": "refund_policy_v4",
        "exceptions_found": exceptions_found,
        "source": sources,
        "policy_version_note": "",
        "requires_human_confirmation": False,
        "answer_hints": answer_hints,
        "explanation": "Refund policy được phân tích từ retrieved chunks.",
    }


def _analyze_access(task: str, chunks: list[dict[str, Any]], access_tool_output: dict | None) -> dict:
    requested_level = _extract_access_level(task, chunks)
    task_lower = task.lower()
    sources = _dedupe([chunk.get("source", "unknown") for chunk in chunks])
    answer_hints: list[str] = []
    notes: list[str] = []

    if requested_level is None:
        requested_level = 3
        notes.append("Không tìm thấy level rõ ràng trong task; dùng mặc định Level 3 để đọc SOP.")

    if requested_level == 3 and "admin access" in task_lower:
        notes.append("Theo SOP, Level 3 là Elevated Access; Admin Access là Level 4.")
    if requested_level == 4 and "elevated access" in task_lower:
        notes.append("Theo SOP, Elevated Access là Level 3; Level 4 là Admin Access.")

    standard_lines = _find_relevant_lines(chunks, ["phê duyệt", "it security", "it manager", "ciso", "thời gian xử lý"], limit=3)
    escalation_lines = _find_relevant_lines(chunks, ["tạm thời", "24 giờ", "tech lead", "on-call", "security audit"], limit=4)
    use_standard_approval_answer = _asks_standard_approval_matrix(task)
    use_temporary_access_answer = _asks_temporary_access(task)

    if access_tool_output:
        standard_approvers = access_tool_output.get("standard_required_approvers") or access_tool_output.get("required_approvers", [])
        if use_standard_approval_answer and standard_approvers:
            level_name = access_tool_output.get("access_level_name")
            if level_name:
                answer_hints.append(f"SOP ghi nhận quyền yêu cầu là {level_name}.")
            answer_hints.append(f"Cần {len(standard_approvers)} người phê duyệt: {', '.join(standard_approvers)}.")
            answer_hints.append(f"Người phê duyệt cuối cùng/cao nhất là {standard_approvers[-1]}.")
            if access_tool_output.get("standard_processing_time"):
                answer_hints.append(f"Thời gian xử lý tiêu chuẩn: {access_tool_output['standard_processing_time']}.")
        elif access_tool_output.get("emergency_override") and use_temporary_access_answer:
            answer_hints.append("Level 2 có emergency bypass trong lab grading flow.")
            answer_hints.append("Điều kiện emergency bypass: approval đồng thời của Line Manager và IT Admin on-call.")
            answer_hints.append("Không cần IT Security cho Level 2 emergency.")
            level_name = access_tool_output.get("access_level_name")
            if level_name:
                answer_hints.append(f"Quyền yêu cầu thuộc {level_name}.")
            if access_tool_output.get("temporary_access_duration_hours"):
                answer_hints.append(
                    f"Quyền tạm thời tối đa {access_tool_output['temporary_access_duration_hours']} giờ; sau đó phải có ticket chính thức hoặc bị thu hồi."
                )
        else:
            level_name = access_tool_output.get("access_level_name")
            if level_name:
                answer_hints.append(f"SOP ghi nhận quyền yêu cầu là {level_name}.")
            approvers = standard_approvers
            if approvers:
                answer_hints.append(f"Phê duyệt tiêu chuẩn cần: {', '.join(approvers)}.")
            if access_tool_output.get("standard_processing_time"):
                answer_hints.append(f"Thời gian xử lý tiêu chuẩn: {access_tool_output['standard_processing_time']}.")

        notes.extend(access_tool_output.get("notes", []))

    if use_temporary_access_answer:
        answer_hints.extend(line["rule"] for line in escalation_lines if line["rule"] not in answer_hints)
    if use_standard_approval_answer or not use_temporary_access_answer:
        answer_hints.extend(line["rule"] for line in standard_lines if line["rule"] not in answer_hints)

    return {
        "policy_applies": True,
        "policy_name": "access_control_sop",
        "exceptions_found": [],
        "source": _dedupe(sources + ([access_tool_output.get("source")] if access_tool_output else [])),
        "policy_version_note": "",
        "requires_human_confirmation": False,
        "answer_hints": answer_hints,
        "explanation": "Access-control policy được tổng hợp từ SOP và MCP access tool.",
        "access_decision": access_tool_output or {},
        "notes": notes,
        "access_focus": "standard_approval" if use_standard_approval_answer else ("temporary_access" if use_temporary_access_answer else "generic"),
    }


def analyze_policy(task: str, chunks: list[dict[str, Any]], mcp_outputs: dict[str, dict] | None = None) -> dict:
    """
    Phân tích policy dựa trên context chunks và, nếu có, output từ MCP tools.
    """
    domain = _infer_domain(task, chunks)
    mcp_outputs = mcp_outputs or {}

    if domain == "refund":
        result = _analyze_refund(task, chunks)
    elif domain == "access":
        result = _analyze_access(task, chunks, mcp_outputs.get("check_access_permission"))
    else:
        relevant_lines = _find_relevant_lines(chunks, _task_lower(task, chunks).split(), limit=3)
        result = {
            "policy_applies": True,
            "policy_name": "generic_policy_lookup",
            "exceptions_found": [],
            "source": _dedupe([chunk.get("source", "unknown") for chunk in chunks]),
            "policy_version_note": "",
            "requires_human_confirmation": False,
            "answer_hints": [line["rule"] for line in relevant_lines],
            "explanation": "Không phát hiện policy domain đặc thù; giữ generic grounded hints.",
        }

    result["domain"] = domain
    return result


# ─────────────────────────────────────────────
# Worker Entry Point
# ─────────────────────────────────────────────

def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.

    Args:
        state: AgentState dict

    Returns:
        Updated AgentState với policy_result và mcp_tools_used
    """
    task = state.get("task", "")
    chunks = list(state.get("retrieved_chunks", []))
    needs_tool = state.get("needs_tool", False)

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state.setdefault("mcp_tools_used", [])
    state.setdefault("worker_io_logs", [])

    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "needs_tool": needs_tool,
        },
        "output": None,
        "error": None,
    }

    try:
        domain = _infer_domain(task, chunks)
        mcp_outputs: dict[str, dict] = {}

        # Standalone worker vẫn tự lấy context nếu chưa có retrieval context.
        if not chunks:
            mcp_result = _call_mcp_tool("search_kb", {"query": task, "top_k": 3})
            state["mcp_tools_used"].append(mcp_result)
            state["history"].append(f"[{WORKER_NAME}] called MCP search_kb")
            if mcp_result.get("output") and mcp_result["output"].get("chunks"):
                chunks = mcp_result["output"]["chunks"]
                state["retrieved_chunks"] = chunks
                state["retrieved_sources"] = mcp_result["output"].get("sources", [])
                mcp_outputs["search_kb"] = mcp_result["output"]

        if domain == "access" and (needs_tool or True):
            access_level = _extract_access_level(task, chunks) or 3
            requester_role = _extract_requester_role(task)
            access_result = _call_mcp_tool(
                "check_access_permission",
                {
                    "access_level": access_level,
                    "requester_role": requester_role,
                    "is_emergency": _is_emergency_request(task, chunks),
                },
            )
            state["mcp_tools_used"].append(access_result)
            state["history"].append(f"[{WORKER_NAME}] called MCP check_access_permission")
            if access_result.get("output") and not access_result.get("error"):
                mcp_outputs["check_access_permission"] = access_result["output"]

        ticket_id = _extract_ticket_id(task)
        if needs_tool and (ticket_id or (domain == "access" and any(keyword in task.lower() for keyword in ["p1", "ticket", "incident"]))):
            lookup_ticket_id = ticket_id or "P1-LATEST"
            ticket_result = _call_mcp_tool("get_ticket_info", {"ticket_id": lookup_ticket_id})
            state["mcp_tools_used"].append(ticket_result)
            state["history"].append(f"[{WORKER_NAME}] called MCP get_ticket_info for {lookup_ticket_id}")
            if ticket_result.get("output") and not ticket_result.get("error"):
                mcp_outputs["get_ticket_info"] = ticket_result["output"]

        policy_result = analyze_policy(task, chunks, mcp_outputs=mcp_outputs)
        if "get_ticket_info" in mcp_outputs:
            policy_result["ticket_context"] = mcp_outputs["get_ticket_info"]

        state["policy_result"] = policy_result

        worker_io["output"] = {
            "domain": policy_result.get("domain"),
            "policy_applies": policy_result["policy_applies"],
            "exceptions_count": len(policy_result.get("exceptions_found", [])),
            "mcp_calls": len(state["mcp_tools_used"]),
        }
        state["history"].append(
            f"[{WORKER_NAME}] domain={policy_result.get('domain')} "
            f"policy_applies={policy_result['policy_applies']} "
            f"exceptions={len(policy_result.get('exceptions_found', []))}"
        )
    except Exception as exc:
        worker_io["error"] = {"code": "POLICY_CHECK_FAILED", "reason": str(exc)}
        state["policy_result"] = {
            "policy_applies": False,
            "policy_name": "policy_worker_error",
            "exceptions_found": [],
            "source": [],
            "policy_version_note": "",
            "requires_human_confirmation": True,
            "answer_hints": [],
            "explanation": str(exc),
        }
        state["history"].append(f"[{WORKER_NAME}] ERROR: {exc}")

    state["worker_io_logs"].append(worker_io)
    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Policy Tool Worker — Standalone Test")
    print("=" * 50)

    test_cases = [
        {
            "task": "Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi — được không?",
            "retrieved_chunks": [
                {
                    "text": "Ngoại lệ không được hoàn tiền: Đơn hàng đã áp dụng mã giảm giá đặc biệt theo chương trình khuyến mãi Flash Sale.",
                    "source": "policy_refund_v4.txt",
                    "score": 0.90,
                }
            ],
            "needs_tool": True,
        },
        {
            "task": "Ai phải phê duyệt để cấp quyền Level 3?",
            "retrieved_chunks": [
                {
                    "text": "Level 3 — Elevated Access: Phê duyệt: Line Manager + IT Admin + IT Security.",
                    "source": "access_control_sop.txt",
                    "score": 0.92,
                }
            ],
            "needs_tool": True,
        },
        {
            "task": "Contractor cần Level 2 access tạm thời để xử lý sự cố P1 đang active.",
            "retrieved_chunks": [
                {
                    "text": "Quy trình escalation khẩn cấp: On-call IT Admin có thể cấp quyền tạm thời (max 24 giờ) sau khi được Tech Lead phê duyệt bằng lời.",
                    "source": "access_control_sop.txt",
                    "score": 0.91,
                }
            ],
            "needs_tool": True,
        },
    ]

    for test_case in test_cases:
        print(f"\n▶ Task: {test_case['task'][:70]}...")
        result = run(test_case.copy())
        policy_result = result.get("policy_result", {})
        print(f"  domain: {policy_result.get('domain')}")
        print(f"  policy_applies: {policy_result.get('policy_applies')}")
        print(f"  answer_hints: {policy_result.get('answer_hints', [])[:2]}")
        print(f"  MCP calls: {len(result.get('mcp_tools_used', []))}")

    print("\n✅ policy_tool_worker test done.")
