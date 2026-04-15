# Quality report — Lab Day 10 (nhóm)

**run_id:** inject-good-cp3-20260415 (so sánh với inject-bad-cp3-20260415)  
**Ngày:** 2026-04-15

---

## 1. Tóm tắt số liệu

| Chỉ số | Trước | Sau | Ghi chú |
|--------|-------|-----|---------|
| raw_records | 11 | 11 | Cùng dùng `data/raw/policy_export_inject.csv` |
| cleaned_records | 7 | 7 | Run xấu giữ lại 2 chunk refund stale; run tốt giữ cùng số lượng nhưng đã clean đúng claim |
| quarantine_records | 4 | 4 | Quarantine giữ nguyên 4 nhóm lỗi: HR conflict, duplicate bẩn, `exported_at` trước hiệu lực, `exported_at` ở tương lai |
| Expectation halt? | Có | Không | `inject-bad-cp3-20260415` fail ở `refund_no_stale_14d_window`; `inject-good-cp3-20260415` pass toàn bộ |

---

## 2. Before / after retrieval (bắt buộc)

> Đính kèm hoặc dẫn link tới `artifacts/eval/before_after_eval.csv` (hoặc 2 file before/after).

File dùng để so sánh là `artifacts/eval/eval_inject-bad-cp3-20260415.csv` và `artifacts/eval/eval_inject-good-cp3-20260415.csv`.

**Câu hỏi then chốt:** refund window (`q_refund_window`)  
**Trước:** dòng trong CSV của run xấu là `q_refund_window,...,contains_expected=yes,hits_forbidden=yes,...`. Điều này cho thấy top-k vẫn còn chunk chứa `14 ngày làm việc`, dù top-1 đã là `policy_refund_v4` với preview đúng.  
**Sau:** dòng trong CSV của run tốt là `q_refund_window,...,contains_expected=yes,hits_forbidden=no,...`. Sự thay đổi quan trọng ở đây là `hits_forbidden` chuyển từ `yes` sang `no`, đúng mục tiêu quan sát stale chunk ở tầng dữ liệu.

**Merit (khuyến nghị):** versioning HR — `q_leave_version` (`contains_expected`, `hits_forbidden`, cột `top1_doc_expected`)

**Trước:** `q_leave_version,...,contains_expected=yes,hits_forbidden=no,top1_doc_expected=yes,...`  
**Sau:** `q_leave_version,...,contains_expected=yes,hits_forbidden=no,top1_doc_expected=yes,...`

Ca HR không được dùng như ví dụ “xấu rồi tốt lên”. Thay vào đó, nó là bằng chứng bổ sung rằng cleaning rule đã chặn row `10 ngày phép năm` ở quarantine nhưng vẫn giữ được chunk HR hiện hành trong cleaned snapshot.

Bổ sung cho vòng chấm cuối, file `artifacts/eval/grading_run.jsonl` đã được tạo trên snapshot hiện tại. Artifact này có đủ 3 dòng `gq_d10_01`, `gq_d10_02`, `gq_d10_03`; cả 3 đều `contains_expected=true`, và `gq_d10_03` còn có `top1_doc_matches=true`.

---

## 3. Freshness & monitor

Trong các artifact hiện tại, freshness chạy với SLA runtime 24 giờ, thể hiện ở log qua `sla_hours: 24.0`; contract cũng đang ghi cùng giá trị 24 giờ nhưng code hiện lấy SLA từ `FRESHNESS_SLA_HOURS` trong env/default. Với baseline `baseline-cp1-20260415`, log persisted ghi `freshness_check=FAIL {"latest_exported_at": "2026-04-10T08:00:00", "age_hours": 117.629, "sla_hours": 24.0, "reason": "freshness_sla_exceeded"}`. Ngược lại, `inject-good-cp3-20260415` trả về `PASS` với `age_hours=21.653`. Tôi giữ cả hai kết quả trong báo cáo để phân biệt rõ giữa “pipeline chạy đúng” và “data snapshot còn trong SLA”.

---

## 4. Corruption inject (Sprint 3)

File inject là `data/raw/policy_export_inject.csv`. Tôi cố ý đưa vào bốn nhóm lỗi. Một là refund stale với hai row còn `14 ngày làm việc`; run xấu để nguyên nên expectation fail và eval có `hits_forbidden=yes`. Hai là HR claim conflict: row ghi `10 ngày phép năm` dù metadata 2026 bị đưa vào quarantine với reason `hr_policy_claim_conflict`. Ba là duplicate/text bẩn: một row IT có BOM ở đầu chuỗi bị bắt thành `duplicate_chunk_text` sau bước sanitize trước dedupe. Bốn là metadata/timestamp lỗi: một row SLA có `exported_at` trước `effective_date`, một row khác có `exported_at` ở tương lai; cả hai đều vào quarantine.

---

## 5. Hạn chế & việc chưa làm

- `artifacts/eval/grading_run.jsonl` đã được tạo theo snapshot hiện tại; nếu collection active đổi sau đó thì cần chạy lại grading để artifact tiếp tục khớp trạng thái thật.
- `canonical_claims` trong contract mới đi sâu cho refund và HR; các tài liệu còn lại hiện chủ yếu được bảo vệ bằng schema và metadata.
- Eval hiện là keyword-based nên phù hợp để chứng minh before/after ở tầng dữ liệu, nhưng chưa phải là đánh giá semantic đầy đủ.
