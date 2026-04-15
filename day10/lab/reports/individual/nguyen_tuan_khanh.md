# Báo Cáo Cá Nhân — Lab Day 10: Data Pipeline & Observability

**Họ và tên:** Nguyễn Tuấn Khanh  
**Vai trò:** Ingestion / Cleaning / Embed / Monitoring — phụ trách toàn bộ pipeline và tài liệu  
**Ngày nộp:** 2026-04-15  
**Độ dài yêu cầu:** **400–650 từ** (ngắn hơn Day 09 vì rubric slide cá nhân ~10% — vẫn phải đủ bằng chứng)

---

> Viết **"tôi"**, đính kèm **run_id**, **tên file**, **đoạn log** hoặc **dòng CSV** thật.  
> Nếu làm phần clean/expectation: nêu **một số liệu thay đổi** (vd `quarantine_records`, `hits_forbidden`, `top1_doc_expected`) khớp bảng `metric_impact` của nhóm.  
> Lưu: `reports/individual/[ten_ban].md`

---

## 1. Tôi phụ trách phần nào? (80–120 từ)

**File / module:**

- `etl_pipeline.py`
- `transform/cleaning_rules.py`
- `quality/expectations.py`
- `monitoring/freshness_check.py`
- `contracts/data_contract.yaml`
- `docs/*.md` và `reports/*.md`

**Kết nối với thành viên khác:**

Vì nhóm chỉ có một thành viên, tôi tự làm toàn bộ vai trò từ ingest, cleaning, embed, monitoring đến viết báo cáo. Điều này buộc tôi phải giữ kiến trúc đơn giản và trace được bằng artifact thật.

**Bằng chứng (commit / comment trong code):**

Bằng chứng rõ nhất là ba run có log và manifest thật: `baseline-cp1-20260415`, `inject-bad-cp3-20260415`, `inject-good-cp3-20260415`. Các thay đổi chính nằm trong `transform/cleaning_rules.py`, `quality/expectations.py` và `contracts/data_contract.yaml`, rồi được phản ánh lại trong `artifacts/logs/`, `artifacts/manifests/`, `artifacts/quarantine/` và `artifacts/eval/`.

---

## 2. Một quyết định kỹ thuật (100–150 từ)

Quyết định kỹ thuật quan trọng nhất của tôi là đưa versioning và canonical claims về `contracts/data_contract.yaml` thay vì để hard-code trong code. Cụ thể, `allowed_doc_ids`, `policy_versioning.hr_leave_min_effective_date`, `canonical_claims` và `metadata_rules.exported_at` đều được đọc từ contract qua `contract_config.py`. Tôi chọn cách này vì rubric Distinction yêu cầu tránh hard-code cutoff version.

Tôi cũng tách rõ `halt` và `warn`. Với dữ liệu ảnh hưởng trực tiếp đến correctness như refund stale hoặc schema ngày, tôi để `halt`. Với `chunk_min_length_8`, tôi giữ `warn` vì đây là tín hiệu chất lượng chứ chưa phải lỗi buộc dừng publish. Nhờ vậy, `inject-bad-cp3-20260415` fail đúng ở `refund_no_stale_14d_window`, còn `inject-good-cp3-20260415` pass toàn bộ expectation.

---

## 3. Một lỗi hoặc anomaly đã xử lý (100–150 từ)

Anomaly tôi xử lý rõ nhất là refund stale trong file inject. Ở run xấu `inject-bad-cp3-20260415`, log ghi `expectation[refund_no_stale_14d_window] FAIL (halt) :: violations=2`. Tôi vẫn cho run này embed bằng `--skip-validate` để lấy evidence before. Sau đó tôi chạy lại cùng raw inject nhưng bật clean chuẩn, kết quả ở `inject-good-cp3-20260415` là `refund_no_stale_14d_window OK (halt) :: violations=0`.

Song song, tôi thêm các rule để bắt anomaly không nằm ở nội dung trả lời nhưng vẫn ảnh hưởng pipeline: một row HR `10 ngày phép năm` bị đẩy vào quarantine với `hr_policy_claim_conflict`, một row IT chứa BOM bị thành `duplicate_chunk_text`, và hai row SLA lỗi `exported_at` bị quarantine với reason riêng. Những anomaly này đều có thể xem trực tiếp trong `artifacts/quarantine/quarantine_inject-good-cp3-20260415.csv`.

---

## 4. Bằng chứng trước / sau (80–120 từ)

Before/after chính của tôi là `q_refund_window`. Trong `artifacts/eval/eval_inject-bad-cp3-20260415.csv`, dòng này có `contains_expected=yes` nhưng `hits_forbidden=yes`. Sau khi clean và publish lại, `artifacts/eval/eval_inject-good-cp3-20260415.csv` chuyển thành `contains_expected=yes` và `hits_forbidden=no`. Tôi xem đây là bằng chứng mạnh nhất vì nó chứng minh top-k context đã sạch hơn chứ không chỉ top-1 đẹp hơn.

Tôi dùng `q_leave_version` như guardrail bổ sung. Cả trước và sau đều giữ `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes`, cho thấy rule HR không làm mất chunk đúng.

---

## 5. Cải tiến tiếp theo (40–80 từ)

Nếu có thêm 2 giờ, tôi sẽ thêm một script regression nhỏ để tự động chạy cặp `inject-bad` và `inject-good`, sau đó xuất một bảng so sánh log, quarantine reason, eval CSV và `grading_run.jsonl` trong một artifact duy nhất.
