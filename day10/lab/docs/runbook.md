# Runbook — Lab Day 10 (incident tối giản)

---

## Symptom

User hoặc agent trả lời sai cửa sổ hoàn tiền là `14 ngày làm việc` thay vì `7 ngày làm việc`, hoặc trả lời đúng top-1 nhưng retrieval vẫn kéo theo chunk stale trong top-k. Một dấu hiệu khác là pipeline run xong nhưng freshness báo `FAIL`, khiến snapshot không phù hợp để publish cho truy vấn hiện hành.

---

## Detection

Tôi ưu tiên kiểm tra theo thứ tự: expectation log, eval retrieval, rồi freshness. Với run xấu `inject-bad-cp3-20260415`, log ghi `expectation[refund_no_stale_14d_window] FAIL (halt) :: violations=2`, còn file `artifacts/eval/eval_inject-bad-cp3-20260415.csv` cho thấy `q_refund_window` có `hits_forbidden=yes`. Ở baseline `baseline-cp1-20260415`, log persisted ghi `freshness_check=FAIL` với `age_hours=117.629`, cho thấy snapshot đã vượt SLA 24 giờ tại thời điểm run.

---

## Diagnosis

| Bước | Việc làm | Kết quả mong đợi |
|------|----------|------------------|
| 1 | Kiểm tra `artifacts/manifests/*.json` và `artifacts/logs/run_<run_id>.log` | Xác định đúng `run_id`, số lượng raw/cleaned/quarantine và xem expectation nào fail |
| 2 | Mở `artifacts/quarantine/*.csv` | Nhìn thấy reason cụ thể như `hr_policy_claim_conflict`, `duplicate_chunk_text`, `exported_at_in_future` để biết lỗi nằm ở nội dung hay metadata |
| 3 | So sánh `artifacts/eval/eval_inject-bad-cp3-20260415.csv` với `artifacts/eval/eval_inject-good-cp3-20260415.csv` | Xác nhận ảnh hưởng thật lên retrieval, đặc biệt `q_refund_window` từ `hits_forbidden=yes` về `hits_forbidden=no` |

---

## Mitigation

Nếu issue là refund stale hoặc conflict version, tôi sửa ở contract hoặc cleaning rule rồi rerun pipeline chuẩn, không dùng `--skip-validate`. Nếu snapshot xấu đã từng được embed cho mục đích demo, tôi rerun ngay snapshot tốt để Chroma prune id cũ và publish lại manifest mới. Nếu freshness `FAIL` vì raw export thực sự cũ như baseline, tôi giữ trạng thái `FAIL` trung thực trong manifest và runbook thay vì ép pass bằng cách sửa tay timestamp.

---

## Prevention

Giữ `contracts/data_contract.yaml` là source of truth cho `allowed_doc_ids`, cutoff HR và canonical claims để tránh hard-code rải rác trong code. Duy trì file inject riêng `data/raw/policy_export_inject.csv` để regression-test các lỗi stale refund, HR conflict, duplicate bẩn và metadata sai. Chỉ dùng `--skip-validate` cho demo có chủ đích; run publish chuẩn phải để expectation halt hoạt động bình thường. Sau khi snapshot tốt đã publish, chạy `grading_run.py` để sinh `artifacts/eval/grading_run.jsonl`; nếu collection đổi sau đó thì rerun grading để artifact tiếp tục khớp snapshot active.
