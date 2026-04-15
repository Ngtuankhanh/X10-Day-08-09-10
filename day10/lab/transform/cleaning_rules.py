"""
Cleaning rules — raw export → cleaned rows + quarantine.

Baseline gồm các failure mode mở rộng (allowlist doc_id, parse ngày, HR stale version).
Sinh viên thêm ≥3 rule mới: mỗi rule phải ghi `metric_impact` (xem README — chống trivial).
"""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from contract_config import load_contract

_CONTRACT = load_contract()
ALLOWED_DOC_IDS = frozenset(_CONTRACT.get("allowed_doc_ids", []))
HR_MIN_EFFECTIVE_DATE = str(
    _CONTRACT.get("policy_versioning", {}).get("hr_leave_min_effective_date", "2026-01-01")
)
REFUND_CLAIMS = _CONTRACT.get("canonical_claims", {}).get("policy_refund_v4", {})
HR_CLAIMS = _CONTRACT.get("canonical_claims", {}).get("hr_leave_policy", {})
EXPORTED_AT_RULES = _CONTRACT.get("metadata_rules", {}).get("exported_at", {})

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _norm_text(s: str) -> str:
    return " ".join(_sanitize_chunk_text(s).strip().split()).lower()


def _sanitize_chunk_text(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_CHARS.sub("", text)
    text = _CONTROL_CHARS.sub(" ", text)
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _stable_chunk_id(doc_id: str, chunk_text: str, seq: int) -> str:
    h = hashlib.sha256(f"{doc_id}|{chunk_text}|{seq}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}_{seq}_{h}"


def _normalize_effective_date(raw: str) -> Tuple[str, str]:
    """
    Trả về (iso_date, error_reason).
    iso_date rỗng nếu không parse được.
    """
    s = (raw or "").strip()
    if not s:
        return "", "empty_effective_date"
    if _ISO_DATE.match(s):
        return s, ""
    m = _DMY_SLASH.match(s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}", ""
    return "", "invalid_effective_date_format"


def _normalize_exported_at(raw: str) -> Tuple[str, datetime | None, str]:
    s = (raw or "").strip()
    if not s:
        return "", None, "missing_exported_at"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return "", None, "invalid_exported_at_format"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z"), dt, ""


def load_raw_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def clean_rows(
    rows: List[Dict[str, str]],
    *,
    apply_refund_window_fix: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Trả về (cleaned, quarantine).

    Baseline (mở rộng theo narrative Day 10):
    1) Quarantine: doc_id không thuộc allowlist (export lạ / catalog sai).
    2) Chuẩn hoá effective_date sang YYYY-MM-DD; quarantine nếu không parse được.
    3) Quarantine: chunk hr_leave_policy có effective_date < 2026-01-01 (bản HR cũ / conflict version).
    4) Quarantine: chunk_text rỗng hoặc effective_date rỗng sau chuẩn hoá.
    5) Loại trùng nội dung chunk_text (giữ bản đầu).
    6) Chuẩn hoá / kiểm tra exported_at; quarantine nếu thiếu, sai format, ở tương lai,
       hoặc đứng trước effective_date.
    7) Làm sạch invisible/control chars trước dedupe để bắt duplicate bẩn.
    8) Contract-driven versioning: HR stale cutoff đọc từ contract; refund stale 14 ngày
       được sửa theo contract nếu bật fix, còn HR claim sai nội dung thì quarantine.
    """
    quarantine: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    cleaned: List[Dict[str, Any]] = []
    seq = 0
    now_utc = datetime.now(timezone.utc)

    for raw in rows:
        doc_id = raw.get("doc_id", "")
        text = raw.get("chunk_text", "")
        eff_raw = raw.get("effective_date", "")
        exported_at = raw.get("exported_at", "")

        if doc_id not in ALLOWED_DOC_IDS:
            quarantine.append({**raw, "reason": "unknown_doc_id"})
            continue

        eff_norm, eff_err = _normalize_effective_date(eff_raw)
        if eff_err == "empty_effective_date":
            quarantine.append({**raw, "reason": "missing_effective_date"})
            continue
        if eff_err == "invalid_effective_date_format":
            quarantine.append({**raw, "reason": eff_err, "effective_date_raw": eff_raw})
            continue

        if doc_id == "hr_leave_policy" and eff_norm < HR_MIN_EFFECTIVE_DATE:
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_policy_effective_date",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        text = _sanitize_chunk_text(text)
        if not text:
            quarantine.append({**raw, "reason": "missing_chunk_text"})
            continue

        exported_norm, exported_dt, exported_err = _normalize_exported_at(exported_at)
        if EXPORTED_AT_RULES.get("required", True) and exported_err == "missing_exported_at":
            quarantine.append({**raw, "reason": exported_err})
            continue
        if exported_err == "invalid_exported_at_format":
            quarantine.append({**raw, "reason": exported_err})
            continue
        if exported_dt and EXPORTED_AT_RULES.get("must_not_be_in_future", True) and exported_dt > now_utc:
            quarantine.append({**raw, "reason": "exported_at_in_future", "exported_at_normalized": exported_norm})
            continue
        if (
            exported_dt
            and EXPORTED_AT_RULES.get("must_be_on_or_after_effective_date", True)
            and exported_dt.date().isoformat() < eff_norm
        ):
            quarantine.append(
                {
                    **raw,
                    "reason": "exported_at_before_effective_date",
                    "effective_date_normalized": eff_norm,
                    "exported_at_normalized": exported_norm,
                }
            )
            continue

        key = _norm_text(text)
        if key in seen_text:
            quarantine.append({**raw, "reason": "duplicate_chunk_text"})
            continue
        seen_text.add(key)

        fixed_text = text
        if apply_refund_window_fix and doc_id == "policy_refund_v4":
            refund_target = str(REFUND_CLAIMS.get("current_window_text", "7 ngày làm việc"))
            if "14 ngày làm việc" in fixed_text:
                fixed_text = fixed_text.replace(
                    "14 ngày làm việc",
                    refund_target,
                )
                fixed_text += " [cleaned: stale_refund_window]"

        hr_forbidden = [str(s).lower() for s in HR_CLAIMS.get("forbidden_any", [])]
        if doc_id == "hr_leave_policy" and any(token in fixed_text.lower() for token in hr_forbidden):
            quarantine.append(
                {
                    **raw,
                    "reason": "hr_policy_claim_conflict",
                    "effective_date_normalized": eff_norm,
                    "exported_at_normalized": exported_norm,
                }
            )
            continue

        seq += 1
        cleaned.append(
            {
                "chunk_id": _stable_chunk_id(doc_id, fixed_text, seq),
                "doc_id": doc_id,
                "chunk_text": fixed_text,
                "effective_date": eff_norm,
                "exported_at": exported_norm,
            }
        )

    return cleaned, quarantine


def write_cleaned_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at\n", encoding="utf-8")
        return
    fieldnames = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def write_quarantine_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at,reason\n", encoding="utf-8")
        return
    keys: List[str] = []
    seen_k: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen_k:
                seen_k.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
