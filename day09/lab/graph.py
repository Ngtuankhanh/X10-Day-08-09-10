"""
graph.py — Supervisor Orchestrator
Sprint 1: Implement AgentState, supervisor_node, route_decision và kết nối graph.

Kiến trúc:
    Input → Supervisor → [retrieval_worker | policy_tool_worker | human_review] → synthesis → Output

Chạy thử:
    python graph.py
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Literal, Optional, TypedDict

from workers.policy_tool import run as policy_tool_run
from workers.retrieval import run as retrieval_run
from workers.synthesis import run as synthesis_run


# ─────────────────────────────────────────────
# 1. Shared State — dữ liệu đi xuyên toàn graph
# ─────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    task: str
    top_k: int

    # Supervisor decisions
    route_reason: str
    risk_high: bool
    needs_tool: bool
    hitl_triggered: bool

    # Worker outputs
    retrieved_chunks: list
    retrieved_sources: list
    policy_result: dict
    mcp_tools_used: list
    worker_io_logs: list

    # Final output
    final_answer: str
    sources: list
    confidence: float

    # Trace & history
    history: list
    workers_called: list
    supervisor_route: str
    latency_ms: Optional[int]
    run_id: str
    timestamp: str


def make_initial_state(task: str) -> AgentState:
    """Khởi tạo state cho một run mới."""
    return {
        "task": task,
        "top_k": 3,
        "route_reason": "",
        "risk_high": False,
        "needs_tool": False,
        "hitl_triggered": False,
        "retrieved_chunks": [],
        "retrieved_sources": [],
        "policy_result": {},
        "mcp_tools_used": [],
        "worker_io_logs": [],
        "final_answer": "",
        "sources": [],
        "confidence": 0.0,
        "history": [],
        "workers_called": [],
        "supervisor_route": "",
        "latency_ms": None,
        "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
# 2. Supervisor Node — quyết định route
# ─────────────────────────────────────────────

POLICY_KEYWORDS = [
    "refund",
    "hoàn tiền",
    "flash sale",
    "license",
    "subscription",
    "store credit",
    "access",
    "cấp quyền",
    "approval matrix",
    "level 1",
    "level 2",
    "level 3",
    "level 4",
    "admin access",
    "elevated access",
]
RETRIEVAL_KEYWORDS = [
    "p1",
    "p2",
    "ticket",
    "sla",
    "escalation",
    "incident",
    "faq",
    "remote",
    "probation",
    "mật khẩu",
    "password",
]
RISK_KEYWORDS = [
    "emergency",
    "khẩn cấp",
    "2am",
    "active incident",
    "prod down",
    "critical",
]


def _matched_keywords(task_lower: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in task_lower]


def supervisor_node(state: AgentState) -> AgentState:
    """
    Supervisor phân tích task và quyết định:
    1. Route sang worker nào
    2. Có cần MCP tool không
    3. Có risk cao cần HITL không
    """
    task_lower = state["task"].lower()
    state["history"].append(f"[supervisor] received task: {state['task'][:120]}")

    policy_matches = _matched_keywords(task_lower, POLICY_KEYWORDS)
    retrieval_matches = _matched_keywords(task_lower, RETRIEVAL_KEYWORDS)
    risk_matches = _matched_keywords(task_lower, RISK_KEYWORDS)
    has_unknown_error = bool(re.search(r"\berr-[a-z0-9-]+\b", task_lower))

    if has_unknown_error:
        route = "human_review"
        needs_tool = False
        risk_high = True
        route_reason = "unknown error code without trusted internal mapping -> human_review | mcp_enabled=False"
    elif policy_matches:
        route = "policy_tool_worker"
        needs_tool = True
        risk_high = bool(risk_matches)
        route_reason = (
            f"policy/access keywords={policy_matches[:4]} -> policy_tool_worker | "
            f"mcp_enabled=True | risk_high={risk_high}"
        )
    elif retrieval_matches:
        route = "retrieval_worker"
        needs_tool = False
        risk_high = bool(risk_matches)
        route_reason = (
            f"retrieval keywords={retrieval_matches[:4]} -> retrieval_worker | "
            f"mcp_enabled=False | risk_high={risk_high}"
        )
    else:
        route = "retrieval_worker"
        needs_tool = False
        risk_high = bool(risk_matches)
        route_reason = f"default retrieval route -> retrieval_worker | mcp_enabled=False | risk_high={risk_high}"

    state["supervisor_route"] = route
    state["route_reason"] = route_reason
    state["needs_tool"] = needs_tool
    state["risk_high"] = risk_high
    state["history"].append(f"[supervisor] route={route} reason={route_reason}")

    return state


# ─────────────────────────────────────────────
# 3. Route Decision — conditional edge
# ─────────────────────────────────────────────

def route_decision(state: AgentState) -> Literal["retrieval_worker", "policy_tool_worker", "human_review"]:
    """Trả về tên bước tiếp theo dựa vào supervisor_route trong state."""
    route = state.get("supervisor_route", "retrieval_worker")
    return route  # type: ignore[return-value]


# ─────────────────────────────────────────────
# 4. Human Review Node — HITL placeholder
# ─────────────────────────────────────────────

def human_review_node(state: AgentState) -> AgentState:
    """
    HITL node: placeholder trong lab mode.
    Supervisor route vẫn giữ nguyên để trace phản ánh quyết định ban đầu.
    """
    state["hitl_triggered"] = True
    state["workers_called"].append("human_review")
    state["history"].append("[human_review] HITL triggered — lab mode auto-approve, then continue to retrieval")

    print("\n⚠️  HITL TRIGGERED")
    print(f"   Task: {state['task']}")
    print(f"   Reason: {state['route_reason']}")
    print("   Action: Auto-approving in lab mode and continuing to retrieval.\n")

    return state


# ─────────────────────────────────────────────
# 5. Worker Wrappers
# ─────────────────────────────────────────────

def retrieval_worker_node(state: AgentState) -> AgentState:
    state["history"].append("[graph] entering retrieval_worker")
    return retrieval_run(state)


def policy_tool_worker_node(state: AgentState) -> AgentState:
    state["history"].append("[graph] entering policy_tool_worker")
    return policy_tool_run(state)


def synthesis_worker_node(state: AgentState) -> AgentState:
    state["history"].append("[graph] entering synthesis_worker")
    return synthesis_run(state)


# ─────────────────────────────────────────────
# 6. Build Graph
# ─────────────────────────────────────────────

def build_graph():
    """
    Xây dựng graph với supervisor-worker pattern.
    Lab này dùng orchestrator Python thuần theo skeleton hiện tại.
    """

    def run(state: AgentState) -> AgentState:
        start = time.time()

        # Step 1: Supervisor decides route
        state = supervisor_node(state)
        route = route_decision(state)

        # Step 2: Execute chosen route
        if route == "human_review":
            state["top_k"] = max(state.get("top_k", 3), 4)
            state = human_review_node(state)
            state = retrieval_worker_node(state)
        elif route == "policy_tool_worker":
            state["top_k"] = max(state.get("top_k", 3), 10)
            state = retrieval_worker_node(state)
            state = policy_tool_worker_node(state)
        else:
            state["top_k"] = max(state.get("top_k", 3), 4)
            state = retrieval_worker_node(state)

        # Step 3: Always synthesize from grounded evidence
        state = synthesis_worker_node(state)

        state["latency_ms"] = int((time.time() - start) * 1000)
        state["timestamp"] = datetime.now().isoformat()
        state["history"].append(f"[graph] completed in {state['latency_ms']}ms")
        return state

    return run


# ─────────────────────────────────────────────
# 7. Public API
# ─────────────────────────────────────────────

_graph = build_graph()


def run_graph(task: str) -> AgentState:
    """
    Entry point: nhận câu hỏi, trả về AgentState với full trace.
    """
    state = make_initial_state(task)
    return _graph(state)


def save_trace(state: AgentState, output_dir: str = "./artifacts/traces") -> str:
    """Lưu trace ra file JSON."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{output_dir}/{state['run_id']}.json"
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    return filename


# ─────────────────────────────────────────────
# 8. Manual Test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Day 09 Lab — Supervisor-Worker Graph")
    print("=" * 60)

    test_queries = [
        "SLA xử lý ticket P1 là bao lâu?",
        "Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi — được không?",
        "Contractor cần Level 2 access tạm thời để xử lý sự cố P1 đang active. Quy trình là gì?",
    ]

    for query in test_queries:
        print(f"\n▶ Query: {query}")
        result = run_graph(query)
        print(f"  Route      : {result['supervisor_route']}")
        print(f"  Reason     : {result['route_reason']}")
        print(f"  Workers    : {result['workers_called']}")
        print(f"  Answer     : {result['final_answer'][:160]}...")
        print(f"  Confidence : {result['confidence']}")
        print(f"  Latency    : {result['latency_ms']}ms")

        trace_file = save_trace(result)
        print(f"  Trace saved → {trace_file}")

    print("\n✅ graph.py test complete.")
