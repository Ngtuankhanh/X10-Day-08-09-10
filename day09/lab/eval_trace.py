"""
eval_trace.py — Trace Evaluation & Comparison
Sprint 4: Chạy pipeline với test questions, phân tích trace, so sánh single vs multi.

Chạy:
    python eval_trace.py                  # Chạy 15 test questions
    python eval_trace.py --grading        # Chạy grading questions (sau 17:00)
    python eval_trace.py --analyze        # Phân tích trace đã có
    python eval_trace.py --compare        # So sánh single vs multi

Outputs:
    artifacts/traces/          — trace của từng câu hỏi
    artifacts/grading_run.jsonl — log câu hỏi chấm điểm
    artifacts/eval_report.json  — báo cáo tổng kết
"""

import json
import os
import sys
import csv
import argparse
from datetime import datetime
from typing import Optional

# Import graph
sys.path.insert(0, os.path.dirname(__file__))
from graph import run_graph, save_trace

LAB_DIR = os.path.dirname(__file__)
DAY08_RESULTS_CSV = os.path.abspath(os.path.join(LAB_DIR, "..", "..", "day08", "lab", "results", "ab_comparison.csv"))
DAY08_GRADING_JSON = os.path.abspath(os.path.join(LAB_DIR, "..", "..", "day08", "lab", "logs", "grading_run.json"))
ABSTAIN_PATTERNS = [
    "không đủ thông tin",
    "không tìm thấy thông tin",
    "do not have enough information",
    "i do not have enough information",
    "i don't have enough information",
]


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(LAB_DIR, path)


def _is_abstention(answer: str) -> bool:
    normalized = " ".join((answer or "").strip().lower().split())
    return any(pattern in normalized for pattern in ABSTAIN_PATTERNS)


def _format_rate(count: int, total: int) -> str:
    if total <= 0:
        return "0/0 (0%)"
    return f"{count}/{total} ({round(100 * count / total)}%)"


def _load_day08_scorecard(csv_path: str) -> dict:
    if not os.path.exists(csv_path):
        return {}

    rows = []
    with open(csv_path, encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("config_label") == "baseline_dense":
                rows.append(row)

    if not rows:
        return {}

    metrics: dict[str, float] = {}
    for metric_name in ["faithfulness", "relevance", "context_recall", "completeness"]:
        values = []
        for row in rows:
            value = row.get(metric_name)
            if value in ("", None, "None"):
                continue
            try:
                values.append(float(value))
            except ValueError:
                continue
        if values:
            metrics[f"avg_{metric_name}"] = round(sum(values) / len(values), 3)

    abstain_count = sum(1 for row in rows if _is_abstention(row.get("answer", "")))
    return {
        "question_count": len(rows),
        "metrics": metrics,
        "abstain_rate": _format_rate(abstain_count, len(rows)),
        "available_metrics": sorted(metrics.keys()),
    }


def _load_day08_grading(grading_path: str) -> dict:
    if not os.path.exists(grading_path):
        return {}

    with open(grading_path, encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list) or not records:
        return {}

    abstain_count = sum(1 for record in records if _is_abstention(record.get("answer", "")))
    chunks = [record.get("chunks_retrieved", 0) for record in records if isinstance(record.get("chunks_retrieved"), (int, float))]
    return {
        "question_count": len(records),
        "retrieval_modes": sorted({record.get("retrieval_mode", "unknown") for record in records}),
        "avg_chunks_retrieved": round(sum(chunks) / len(chunks), 2) if chunks else 0,
        "abstain_rate": _format_rate(abstain_count, len(records)),
    }


# ─────────────────────────────────────────────
# 1. Run Pipeline on Test Questions
# ─────────────────────────────────────────────

def run_test_questions(questions_file: str = "data/test_questions.json") -> list:
    """
    Chạy pipeline với danh sách câu hỏi, lưu trace từng câu.

    Returns:
        list of (question, result) tuples
    """
    questions_path = _resolve_path(questions_file)
    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)

    print(f"\n📋 Running {len(questions)} test questions from {questions_path}")
    print("=" * 60)

    results = []
    for i, q in enumerate(questions, 1):
        question_text = q["question"]
        q_id = q.get("id", f"q{i:02d}")

        print(f"[{i:02d}/{len(questions)}] {q_id}: {question_text[:65]}...")

        try:
            result = run_graph(question_text)
            result["question_id"] = q_id

            # Save individual trace
            trace_file = save_trace(result, _resolve_path("artifacts/traces"))
            print(f"  ✓ route={result.get('supervisor_route', '?')}, "
                  f"conf={result.get('confidence', 0):.2f}, "
                  f"{result.get('latency_ms', 0)}ms")

            results.append({
                "id": q_id,
                "question": question_text,
                "expected_answer": q.get("expected_answer", ""),
                "expected_sources": q.get("expected_sources", []),
                "difficulty": q.get("difficulty", "unknown"),
                "category": q.get("category", "unknown"),
                "result": result,
            })

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            results.append({
                "id": q_id,
                "question": question_text,
                "error": str(e),
                "result": None,
            })

    print(f"\n✅ Done. {sum(1 for r in results if r.get('result'))} / {len(results)} succeeded.")
    return results


# ─────────────────────────────────────────────
# 2. Run Grading Questions (Sprint 4)
# ─────────────────────────────────────────────

def run_grading_questions(questions_file: str = "data/grading_questions.json") -> str:
    """
    Chạy pipeline với grading questions và lưu JSONL log.
    Dùng cho chấm điểm nhóm (chạy sau khi grading_questions.json được public lúc 17:00).

    Returns:
        path tới grading_run.jsonl
    """
    questions_path = _resolve_path(questions_file)
    if not os.path.exists(questions_path):
        print(f"❌ {questions_path} chưa được public (sau 17:00 mới có).")
        return ""

    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)

    os.makedirs(_resolve_path("artifacts"), exist_ok=True)
    output_file = _resolve_path("artifacts/grading_run.jsonl")

    print(f"\n🎯 Running GRADING questions — {len(questions)} câu")
    print(f"   Output → {output_file}")
    print("=" * 60)

    with open(output_file, "w", encoding="utf-8") as out:
        for i, q in enumerate(questions, 1):
            q_id = q.get("id", f"gq{i:02d}")
            question_text = q["question"]
            print(f"[{i:02d}/{len(questions)}] {q_id}: {question_text[:65]}...")

            try:
                result = run_graph(question_text)
                record = {
                    "id": q_id,
                    "question": question_text,
                    "answer": result.get("final_answer", "PIPELINE_ERROR: no answer"),
                    "sources": result.get("sources") or result.get("retrieved_sources", []),
                    "supervisor_route": result.get("supervisor_route", ""),
                    "route_reason": result.get("route_reason", ""),
                    "workers_called": result.get("workers_called", []),
                    "mcp_tools_used": [t.get("tool") for t in result.get("mcp_tools_used", [])],
                    "confidence": result.get("confidence", 0.0),
                    "hitl_triggered": result.get("hitl_triggered", False),
                    "latency_ms": result.get("latency_ms"),
                    "timestamp": datetime.now().isoformat(),
                }
                print(f"  ✓ route={record['supervisor_route']}, conf={record['confidence']:.2f}")
            except Exception as e:
                record = {
                    "id": q_id,
                    "question": question_text,
                    "answer": f"PIPELINE_ERROR: {e}",
                    "sources": [],
                    "supervisor_route": "error",
                    "route_reason": str(e),
                    "workers_called": [],
                    "mcp_tools_used": [],
                    "confidence": 0.0,
                    "hitl_triggered": False,
                    "latency_ms": None,
                    "timestamp": datetime.now().isoformat(),
                }
                print(f"  ✗ ERROR: {e}")

            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\n✅ Grading log saved → {output_file}")
    return output_file


# ─────────────────────────────────────────────
# 3. Analyze Traces
# ─────────────────────────────────────────────

def analyze_traces(traces_dir: str = "artifacts/traces") -> dict:
    """
    Đọc tất cả trace files và tính metrics tổng hợp.

    Metrics:
    - routing_distribution: % câu đi vào mỗi worker
    - avg_confidence: confidence trung bình
    - avg_latency_ms: latency trung bình
    - mcp_usage_rate: % câu có MCP tool call
    - hitl_rate: % câu trigger HITL
    - source_coverage: các tài liệu nào được dùng nhiều nhất

    Returns:
        dict of metrics
    """
    traces_path = _resolve_path(traces_dir)
    if not os.path.exists(traces_path):
        print(f"⚠️  {traces_path} không tồn tại. Chạy run_test_questions() trước.")
        return {}

    trace_files = [f for f in os.listdir(traces_path) if f.endswith(".json")]
    if not trace_files:
        print(f"⚠️  Không có trace files trong {traces_path}.")
        return {}

    traces = []
    for fname in trace_files:
        with open(os.path.join(traces_path, fname), encoding="utf-8") as f:
            traces.append(json.load(f))

    # Compute metrics
    routing_counts = {}
    confidences = []
    latencies = []
    mcp_calls = 0
    hitl_triggers = 0
    abstentions = 0
    source_counts = {}

    for t in traces:
        route = t.get("supervisor_route", "unknown")
        routing_counts[route] = routing_counts.get(route, 0) + 1

        conf = t.get("confidence", 0)
        if conf:
            confidences.append(conf)

        lat = t.get("latency_ms")
        if lat:
            latencies.append(lat)

        if t.get("mcp_tools_used"):
            mcp_calls += 1

        if t.get("hitl_triggered"):
            hitl_triggers += 1

        if _is_abstention(t.get("final_answer", "")):
            abstentions += 1

        trace_sources = t.get("sources") or t.get("retrieved_sources", [])
        for src in trace_sources:
            source_counts[src] = source_counts.get(src, 0) + 1

    total = len(traces)
    metrics = {
        "total_traces": total,
        "routing_distribution": {k: f"{v}/{total} ({100*v//total}%)" for k, v in routing_counts.items()},
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "mcp_usage_rate": f"{mcp_calls}/{total} ({100*mcp_calls//total}%)" if total else "0%",
        "hitl_rate": f"{hitl_triggers}/{total} ({100*hitl_triggers//total}%)" if total else "0%",
        "abstain_rate": _format_rate(abstentions, total),
        "top_sources": sorted(source_counts.items(), key=lambda x: -x[1])[:5],
    }

    return metrics


# ─────────────────────────────────────────────
# 4. Compare Single vs Multi Agent
# ─────────────────────────────────────────────

def compare_single_vs_multi(
    multi_traces_dir: str = "artifacts/traces",
    day08_results_file: Optional[str] = None,
) -> dict:
    """
    So sánh Day 08 (single agent RAG) vs Day 09 (multi-agent).
    Returns:
        dict của comparison metrics
    """
    multi_metrics = analyze_traces(multi_traces_dir)

    scorecard_path = day08_results_file or DAY08_RESULTS_CSV
    day08_scorecard = _load_day08_scorecard(scorecard_path)
    day08_grading = _load_day08_grading(DAY08_GRADING_JSON)

    analysis: dict[str, str] = {
        "routing_visibility": "Day 09 có route_reason cho từng câu và log workers_called; Day 08 không có lớp trace routing tương đương.",
    }

    if day08_scorecard.get("abstain_rate") and multi_metrics.get("abstain_rate"):
        analysis["abstain_rate"] = (
            f"Day 08 baseline abstain_rate={day08_scorecard['abstain_rate']}; "
            f"Day 09 multi-agent abstain_rate={multi_metrics['abstain_rate']}."
        )

    available_day08_metrics = day08_scorecard.get("metrics", {})
    if available_day08_metrics:
        analysis["quality_metrics"] = (
            "Day 08 scorecard available: "
            + ", ".join(f"{name}={value}" for name, value in available_day08_metrics.items())
            + ". Day 09 current eval_trace focuses on orchestration metrics, not LLM-judge scorecards."
        )

    if multi_metrics.get("avg_latency_ms"):
        analysis["latency_note"] = (
            f"Day 09 trace shows avg_latency_ms={multi_metrics['avg_latency_ms']}. "
            "Day 08 checked-in artifacts do not include comparable latency, so no delta is reported."
        )

    comparison = {
        "generated_at": datetime.now().isoformat(),
        "day08_single_agent": {
            "scorecard_baseline": day08_scorecard,
            "grading_run": day08_grading,
        },
        "day09_multi_agent": multi_metrics,
        "analysis": analysis,
    }

    return comparison


# ─────────────────────────────────────────────
# 5. Save Eval Report
# ─────────────────────────────────────────────

def save_eval_report(comparison: dict) -> str:
    """Lưu báo cáo eval tổng kết ra file JSON."""
    os.makedirs(_resolve_path("artifacts"), exist_ok=True)
    output_file = _resolve_path("artifacts/eval_report.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)
    return output_file


# ─────────────────────────────────────────────
# 6. CLI Entry Point
# ─────────────────────────────────────────────

def print_metrics(metrics: dict):
    """Print metrics đẹp."""
    if not metrics:
        return
    print("\n📊 Trace Analysis:")
    for k, v in metrics.items():
        if isinstance(v, list):
            print(f"  {k}:")
            for item in v:
                print(f"    • {item}")
        elif isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk}: {vv}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 09 Lab — Trace Evaluation")
    parser.add_argument("--grading", action="store_true", help="Run grading questions")
    parser.add_argument("--analyze", action="store_true", help="Analyze existing traces")
    parser.add_argument("--compare", action="store_true", help="Compare single vs multi")
    parser.add_argument("--test-file", default="data/test_questions.json", help="Test questions file")
    args = parser.parse_args()

    if args.grading:
        # Chạy grading questions
        log_file = run_grading_questions()
        if log_file:
            print(f"\n✅ Grading log: {log_file}")
            print("   Nộp file này trước 18:00!")

    elif args.analyze:
        # Phân tích traces
        metrics = analyze_traces()
        print_metrics(metrics)

    elif args.compare:
        # So sánh single vs multi
        comparison = compare_single_vs_multi()
        report_file = save_eval_report(comparison)
        print(f"\n📊 Comparison report saved → {report_file}")
        print("\n=== Day 08 vs Day 09 ===")
        for k, v in comparison.get("analysis", {}).items():
            print(f"  {k}: {v}")

    else:
        # Default: chạy test questions
        results = run_test_questions(args.test_file)

        # Phân tích trace
        metrics = analyze_traces()
        print_metrics(metrics)

        # Lưu báo cáo
        comparison = compare_single_vs_multi()
        report_file = save_eval_report(comparison)
        print(f"\n📄 Eval report → {report_file}")
        print("\n✅ Sprint 4 complete!")
        print("   Next: Điền docs/ templates và viết reports/")
