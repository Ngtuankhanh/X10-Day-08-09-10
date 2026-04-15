# Data contract — Lab Day 10

> Bắt đầu từ `contracts/data_contract.yaml` — mở rộng và đồng bộ file này.

---

## 1. Nguồn dữ liệu (source map)

| Nguồn | Phương thức ingest | Failure mode chính | Metric / alert |
|-------|-------------------|-------------------|----------------|
| `data/raw/policy_export_dirty.csv` | CSV export mẫu, đọc bằng `load_raw_csv()` | duplicate, thiếu `effective_date`, `unknown_doc_id`, HR stale version | `raw_records`, `quarantine_records`, `freshness_check` trên manifest baseline |
| `data/raw/policy_export_inject.csv` | CSV inject có chủ đích để kiểm thử quality gate | refund stale, HR claim conflict, duplicate có BOM, `exported_at` sai | expectation log, quarantine reason, eval before/after |
| `data/docs/*.txt` được ánh xạ trong `contracts/data_contract.yaml` | Nguồn canonical để điền `canonical_sources`, `allowed_doc_ids`, `canonical_claims` | code hard-code cutoff hoặc claim hiện hành lệch contract | review contract, expectation contract-driven, đối chiếu artifact cleaned |

---

## 2. Schema cleaned

| Cột | Kiểu | Bắt buộc | Ghi chú |
|-----|------|----------|---------|
| chunk_id | string | Có | ID ổn định sinh từ `doc_id`, `chunk_text` sau clean và `seq`; dùng để upsert vào Chroma |
| doc_id | string | Có | Phải thuộc `allowed_doc_ids` trong contract |
| chunk_text | string | Có | Được sanitize trước dedupe; các claim canonical của refund và HR được đối chiếu từ contract |
| effective_date | date | Có | Chuẩn hoá về `YYYY-MM-DD`; HR dùng cutoff `policy_versioning.hr_leave_min_effective_date` |
| exported_at | datetime | Có | Chuẩn hoá về ISO UTC dạng `...Z`; bị quarantine nếu thiếu, sai format, ở tương lai, hoặc trước `effective_date` |

---

## 3. Quy tắc quarantine vs drop

Record bị flag không bị drop im lặng mà được ghi vào `artifacts/quarantine/quarantine_<run_id>.csv`. Trên inject run, file quarantine ghi rõ bốn nhóm lỗi thật: `hr_policy_claim_conflict`, `duplicate_chunk_text`, `exported_at_before_effective_date`, `exported_at_in_future`.

Trong phạm vi nhóm một người, tôi là người review file quarantine trước khi publish lại snapshot. Quy tắc là chỉ cho record quay lại cleaned khi có căn cứ từ contract hoặc từ nguồn canonical trong `data/docs/`. Với `unknown_doc_id` hoặc metadata sai, tôi không merge lại thủ công mà sửa từ raw export hoặc contract mapping trước.

---

## 4. Phiên bản & canonical

Source of truth cho policy refund là `data/docs/policy_refund_v4.txt`, được phản chiếu trong contract qua `canonical_claims.policy_refund_v4.current_window_text = "7 ngày làm việc"`. Vì vậy rule clean không hard-code một ngày cutoff riêng cho refund mà đọc claim hiện hành từ `contracts/data_contract.yaml`.

Source of truth cho policy HR là `data/docs/hr_leave_policy.txt` kết hợp `policy_versioning.hr_leave_min_effective_date = "2026-01-01"` và `canonical_claims.hr_leave_policy.current_annual_leave_text = "12 ngày phép năm"`. Từ đó, pipeline loại bản HR cũ theo ngày hiệu lực và quarantine row có claim `10 ngày phép năm` dù metadata nhìn hợp lệ.
