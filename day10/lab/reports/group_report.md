# Báo Cáo Nhóm — Lab Day 10: Data Pipeline & Data Observability

**Tên nhóm:** X10  
**Thành viên:**
| Tên | Vai trò (Day 10) | Email |
|-----|------------------|-------|
| Nguyễn Tuấn Khanh | Ingestion / Raw Owner; Cleaning & Quality Owner; Embed & Idempotency Owner; Monitoring / Docs Owner | 26ai.khanhnt@vinuni.edu.vn |

**Ngày nộp:** 2026-04-15  
**Repo:** `/Users/nguyenhangan/Project/X10-Day-08-09-10/day10/lab`  
**Độ dài khuyến nghị:** 600–1000 từ

---

> **Nộp tại:** `reports/group_report.md`  
> **Deadline commit:** xem `SCORING.md` (code/trace sớm; report có thể muộn hơn nếu được phép).  
> Phải có **run_id**, **đường dẫn artifact**, và **bằng chứng before/after** (CSV eval hoặc screenshot).

---

## 1. Pipeline tổng quan (150–200 từ)

> Nguồn raw là gì (CSV mẫu / export thật)? Chuỗi lệnh chạy end-to-end? `run_id` lấy ở đâu trong log?

**Tóm tắt luồng:**

Pipeline dùng hai raw CSV trong `data/raw/`: `policy_export_dirty.csv` để khảo sát baseline và `policy_export_inject.csv` để chứng minh corruption inject có kiểm soát. Entry point là `etl_pipeline.py`, đọc raw CSV, chạy cleaning rules trong `transform/cleaning_rules.py`, ghi `artifacts/cleaned/cleaned_<run_id>.csv` và `artifacts/quarantine/quarantine_<run_id>.csv`, sau đó chạy expectation suite trong `quality/expectations.py`. Nếu expectation không halt, pipeline embed lên Chroma collection `day10_kb`, ghi `manifest_<run_id>.json`, rồi kiểm tra freshness qua `monitoring/freshness_check.py`.

Tôi dùng ba mốc evidence chính: `baseline-cp1-20260415`, `inject-bad-cp3-20260415`, và `inject-good-cp3-20260415`. `run_id` xuất hiện ngay đầu log và được lặp lại trong manifest, nên có thể truy ngược một run cụ thể sang cleaned CSV, quarantine CSV và file eval tương ứng. Ví dụ, `artifacts/logs/run_inject-good-cp3-20260415.log` khớp với `artifacts/manifests/manifest_inject-good-cp3-20260415.json` và `artifacts/eval/eval_inject-good-cp3-20260415.csv`.

**Lệnh chạy một dòng (copy từ README thực tế của nhóm):**

`EMBEDDING_LOCAL_FILES_ONLY=1 python3 etl_pipeline.py run --raw data/raw/policy_export_inject.csv --run-id inject-good-cp3-20260415`

---

## 2. Cleaning & expectation (150–200 từ)

> Baseline đã có nhiều rule (allowlist, ngày ISO, HR stale, refund, dedupe…). Nhóm thêm **≥3 rule mới** + **≥2 expectation mới**. Khai báo expectation nào **halt**.

### 2a. Bảng metric_impact (bắt buộc — chống trivial)

| Rule / Expectation mới (tên ngắn) | Trước (số liệu) | Sau / khi inject (số liệu) | Chứng cứ (log / CSV / commit) |
|-----------------------------------|------------------|-----------------------------|-------------------------------|
| `exported_at_validate` | `baseline-cp1-20260415`: 0 dòng quarantine do `exported_at` | `inject-good-cp3-20260415`: 2 dòng quarantine với `exported_at_before_effective_date` và `exported_at_in_future` | `artifacts/quarantine/quarantine_inject-good-cp3-20260415.csv` |
| `sanitize_before_dedupe` | Raw inject có 2 biến thể câu lockout, một dòng chứa BOM ở đầu chuỗi | Sau clean chỉ còn 1 dòng lockout trong cleaned, 1 dòng vào quarantine với `duplicate_chunk_text` | `data/raw/policy_export_inject.csv`, `artifacts/quarantine/quarantine_inject-good-cp3-20260415.csv` |
| `contract_hr_claim_quarantine` | Raw inject có 1 dòng HR metadata 2026 nhưng nội dung `10 ngày phép năm` | Dòng này bị chuyển sang quarantine với `hr_policy_claim_conflict`; cleaned chỉ giữ chunk `12 ngày phép năm` | `artifacts/quarantine/quarantine_inject-good-cp3-20260415.csv`, `artifacts/cleaned/cleaned_inject-good-cp3-20260415.csv` |
| `refund_current_claim_present` | `inject-bad-cp3-20260415`: log ghi `policy_refund_rows=3` và expectation vẫn phải xác nhận snapshot còn claim hiện hành | `inject-good-cp3-20260415`: log tiếp tục xác nhận snapshot tốt vẫn giữ đủ refund rows sau clean | `artifacts/logs/run_inject-bad-cp3-20260415.log`, `artifacts/logs/run_inject-good-cp3-20260415.log` |
| `hr_current_claim_present` | Sau khi rule HR conflict hoạt động, cleaned snapshot còn 1 row HR hợp lệ | Cả run xấu và run tốt đều ghi `hr_rows=1`, cho thấy clean không làm mất chunk HR hiện hành | `artifacts/logs/run_inject-bad-cp3-20260415.log`, `artifacts/logs/run_inject-good-cp3-20260415.log` |

**Rule chính (baseline + mở rộng):**

- Baseline giữ lại allowlist `doc_id`, chuẩn hoá `effective_date`, loại HR stale theo ngày hiệu lực, dedupe và fix refund stale.
- Mở rộng theo hướng Distinction: đọc `allowed_doc_ids`, `hr_leave_min_effective_date`, `canonical_claims`, `metadata_rules.exported_at` từ `contracts/data_contract.yaml` thay vì hard-code trong code.
- Ba cleaning rule mới có tác động trực tiếp trên inject là validate `exported_at`, sanitize text trước dedupe, và quarantine HR claim conflict theo canonical claims trong contract.
- Hai expectation mới là `refund_current_claim_present` và `hr_current_claim_present`; cả hai dùng contract làm source of truth để tránh clean quá tay làm mất claim hiện hành.

**Ví dụ 1 lần expectation fail (nếu có) và cách xử lý:**

Ví dụ rõ nhất là `inject-bad-cp3-20260415`. Log ghi `expectation[refund_no_stale_14d_window] FAIL (halt) :: violations=2`, nhưng tôi chủ động chạy với `--skip-validate` để embed snapshot xấu phục vụ so sánh before/after. Sau đó tôi rerun cùng raw inject nhưng bỏ `--no-refund-fix`, kết quả ở `inject-good-cp3-20260415` là `refund_no_stale_14d_window OK (halt) :: violations=0`.

---

## 3. Before / after ảnh hưởng retrieval hoặc agent (200–250 từ)

> Bắt buộc: inject corruption (Sprint 3) — mô tả + dẫn `artifacts/eval/…` hoặc log.

**Kịch bản inject:**

Tôi tạo file `data/raw/policy_export_inject.csv` riêng để không chạm vào raw gốc. File này cố ý đưa vào bốn loại corruption: refund stale với hai row chứa `14 ngày làm việc`, HR claim conflict với row `10 ngày phép năm` nhưng metadata 2026, duplicate/text bẩn với một row IT có BOM, và metadata/timestamp lỗi với hai row SLA có `exported_at` bất thường. Run xấu dùng lệnh `python3 etl_pipeline.py run --raw data/raw/policy_export_inject.csv --run-id inject-bad-cp3-20260415 --no-refund-fix --skip-validate`, còn run tốt dùng cùng file inject nhưng bỏ hai flag demo.

**Kết quả định lượng (từ CSV / bảng):**

Kết quả rõ nhất nằm ở `q_refund_window`. Trong `artifacts/eval/eval_inject-bad-cp3-20260415.csv`, dòng `q_refund_window` có `contains_expected=yes` nhưng `hits_forbidden=yes`, nghĩa là top-k vẫn còn chunk stale dù câu trả lời nhìn qua có vẻ đúng. Sau khi clean và rerun chuẩn, `artifacts/eval/eval_inject-good-cp3-20260415.csv` chuyển dòng này thành `contains_expected=yes` và `hits_forbidden=no`. Đây là before/after chính của nhóm vì nó thể hiện publish boundary ở tầng dữ liệu đã thay đổi chất lượng retrieval.

Tôi không dùng `q_leave_version` như ví dụ “xấu rồi tốt lên”. Cả hai file eval đều cho `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes`. Giá trị của câu này là làm evidence bổ sung rằng cleaning rules không chỉ chặn bản HR sai mà còn giữ được chunk đúng trong cleaned snapshot. Ngoài retrieval, log quality cũng thay đổi đúng như mong đợi: run xấu fail ở `refund_no_stale_14d_window`, còn run tốt pass toàn bộ expectation.

Sau khi `data/grading_questions.json` được cung cấp, tôi chạy thêm `grading_run.py` và tạo `artifacts/eval/grading_run.jsonl`. File này có đủ 3 dòng `gq_d10_01` đến `gq_d10_03`; cả 3 đều `contains_expected=true`, `hits_forbidden=false`, và câu HR `gq_d10_03` có thêm `top1_doc_matches=true`. Điều này giúp đóng vòng evidence giữa eval before/after của nhóm và artifact grading dùng cho giảng viên.

---

## 4. Freshness & monitoring (100–150 từ)

> SLA bạn chọn, ý nghĩa PASS/WARN/FAIL trên manifest mẫu.

Trong các artifact hiện tại, freshness chạy với SLA runtime 24 giờ; log luôn ghi `sla_hours: 24.0`. Contract cũng đang tài liệu hóa cùng giá trị 24 giờ, nhưng code runtime hiện lấy SLA từ `FRESHNESS_SLA_HOURS` trong env/default. Kết quả không bị làm đẹp: baseline `baseline-cp1-20260415` có log persisted `age_hours=117.629`, nên trả về `FAIL` vì raw export mẫu đã cũ hơn nhiều so với SLA. Ngược lại, inject runs dùng timestamp mới hơn nên cả `inject-bad-cp3-20260415` và `inject-good-cp3-20260415` đều `PASS`; log run tốt ghi `freshness_check=PASS {"latest_exported_at": "2026-04-14T08:16:00Z", "age_hours": 21.653, "sla_hours": 24.0}`. Cách trình bày này giúp phân biệt rõ giữa lỗi dữ liệu stale và lỗi logic clean/expectation.

---

## 5. Liên hệ Day 09 (50–100 từ)

> Dữ liệu sau embed có phục vụ lại multi-agent Day 09 không? Nếu có, mô tả tích hợp; nếu không, giải thích vì sao tách collection.

Data sau embed phục vụ cùng case CS + IT Helpdesk với Day 09, nhưng tôi không sửa code ngoài `day10/lab`. Thay vào đó, tôi publish snapshot vào collection riêng `day10_kb` để quan sát rõ ranh giới ingest, clean, validate và publish. Khi cần nối lại Day 09, collection này có thể được dùng làm nguồn retrieval mới mà không phải thay đổi raw evidence đã tạo cho Day 10.

---

## 6. Rủi ro còn lại & việc chưa làm

- `canonical_claims` mới bao phủ sâu cho refund và HR; nếu mở rộng số lượng policy, contract cần được bổ sung thêm claim hiện hành.
- Eval đang dựa trên keyword và top-k context, nên vẫn có khoảng trống giữa retrieval tốt và answer generation thực tế.
- `grading_run.jsonl` hiện phản ánh snapshot active của collection `day10_kb`; nếu publish snapshot mới thì cần rerun grading để artifact tiếp tục khớp collection.
