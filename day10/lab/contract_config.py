from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "contracts" / "data_contract.yaml"

_DEFAULT_CONTRACT: Dict[str, Any] = {
    "allowed_doc_ids": [
        "policy_refund_v4",
        "sla_p1_2026",
        "it_helpdesk_faq",
        "hr_leave_policy",
    ],
    "policy_versioning": {
        "hr_leave_min_effective_date": "2026-01-01",
    },
    "canonical_claims": {
        "policy_refund_v4": {
            "current_window_text": "7 ngày làm việc",
            "forbidden_any": ["14 ngày làm việc"],
            "required_any": ["7 ngày", "7 ngày làm việc"],
        },
        "hr_leave_policy": {
            "current_annual_leave_text": "12 ngày phép năm",
            "forbidden_any": ["10 ngày phép năm"],
            "required_any": ["12 ngày", "12 ngày phép năm"],
        },
    },
    "metadata_rules": {
        "exported_at": {
            "required": True,
            "must_be_iso_datetime": True,
            "must_not_be_in_future": True,
            "must_be_on_or_after_effective_date": True,
        }
    },
}


@lru_cache(maxsize=1)
def load_contract() -> Dict[str, Any]:
    if not CONTRACT_PATH.is_file():
        return dict(_DEFAULT_CONTRACT)
    with CONTRACT_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _deep_merge(dict(_DEFAULT_CONTRACT), data)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base
