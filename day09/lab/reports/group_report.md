# Báo Cáo Nhóm — Lab Day 09: Multi-Agent Orchestration

**Tên nhóm:** X10  
**Thành viên:**
| Tên | Vai trò | Email |
|-----|---------|-------|
| Nguyễn Tuấn Khanh | Full-stack AI Engineer / Owner | 26ai.khanhnt@vinuni.edu.vn |

**Ngày nộp:** 2026-04-14  
**Repo:** [Ngtuankhanh/X10-Day-08-09-10](https://github.com/Ngtuankhanh/X10-Day-08-09-10)
**Độ dài khuyến nghị:** 600–1000 từ

---

## 1. Kiến trúc nhóm đã xây dựng (150–200 từ)

**Hệ thống tổng quan:**
Nhóm X10 đã xây dựng một hệ thống Multi-Agent Orchestration theo mô hình Supervisor-Worker. Hệ thống bao gồm một Supervisor điều phối chính và 3 Workers chuyên biệt:
1.  **Retrieval Worker**: Chịu trách nhiệm truy xuất dữ liệu từ Vector Database (ChromaDB) sử dụng Dense retrieval.
2.  **Policy Tool Worker**: Xử lý các logic liên quan đến chính sách (hoàn tiền, truy cập) và tích hợp các MCP Tools để kiểm tra điều kiện thực tế.
3.  **Synthesis Worker**: Tổng hợp bằng chứng (grounded evidence) từ các worker trước đó để đưa ra câu trả lời cuối cùng kèm theo citation.
Ngoài ra, hệ thống còn tích hợp cơ chế Human-in-the-loop (HITL) cho các trường hợp risk cao hoặc mã lỗi không xác định.

**Routing logic cốt lõi:**
Supervisor sử dụng **Keyword-based Routing combined with Risk Analysis**. Thay vì dùng LLM classifier tốn kém, tôi định nghĩa các bộ keywords cho `POLICY`, `RETRIEVAL`, và `RISK`. 
- Nếu task chứa keywords về `access`, `refund`, supervisor sẽ route sang `policy_tool_worker`.
- Nếu task chứa keywords về `sla`, `p1`, `ticket`, supervisor sẽ route sang `retrieval_worker`.
- Nếu có `RISK_KEYWORDS` (emergency, prod down), hệ thống sẽ set flag `risk_high=True` để kích hoạt các biện pháp an toàn.

**MCP tools đã tích hợp:**
Hệ thống tích hợp 4 MCP tools thông qua `dispatch_tool`:
- `search_kb`: Tìm kiếm Knowledge Base nội bộ.
- `get_ticket_info`: Tra cứu thông tin ticket Jira (tra cứu được ticket IT-9847 cho P1).
- `check_access_permission`: Kiểm tra approval chain cho 4 cấp độ truy cập dựa trên Access Control SOP.
- `create_ticket`: Tạo ticket mới cho các yêu cầu chưa có trong hệ thống.

---

## 2. Quyết định kỹ thuật quan trọng nhất (200–250 từ)

**Quyết định:** Chuyển đổi từ LLM-as-a-Router sang Deterministic Keyword-based Routing cho Supervisor.

**Bối cảnh vấn đề:**
Trong giai đoạn đầu của Sprint 1, tôi nhận thấy việc gọi LLM chỉ để phân loại câu hỏi (Routing) làm tăng latency đáng kể (trung bình thêm 800-1200ms mỗi request) và đôi khi LLM route sai các câu hỏi chứa thuật ngữ kỹ thuật chồng chéo (ví dụ: một câu hỏi vừa hỏi về P1 SLA vừa hỏi về Access Level).

**Các phương án đã cân nhắc:**

| Phương án | Ưu điểm | Nhược điểm |
|-----------|---------|-----------|
| LLM Classifier | Linh hoạt, hiểu ngữ nghĩa phức tạp. | Latency cao, tốn cost, đôi khi không ổn định (non-deterministic). |
| Keyword-based | Rất nhanh (<10ms), dễ debug, log `route_reason` rõ ràng. | Khó xử lý các câu hỏi quá mơ hồ, cần bảo trì bộ keyword. |

**Phương án đã chọn và lý do:**
Tôi chọn **Keyword-based Routing**. Đối với hệ thống hỗ trợ nội bộ trong lab này, các domain (SLA, Refund, Access) có bộ từ khóa rất đặc trưng. Việc dùng keywords giúp Supervisor phản hồi gần như tức thì, cho phép dành "ngân sách latency" cho các bước quan trọng hơn như Synthesis và Multi-hop retrieval.

**Bằng chứng từ trace/code:**
Trong `graph.py`, logic routing được thực hiện qua hàm `supervisor_node`:
```python
if has_unknown_error:
    route = "human_review"
    route_reason = "unknown error code without trusted internal mapping -> human_review"
elif policy_matches:
    route = "policy_tool_worker"
    route_reason = f"policy/access keywords={policy_matches[:4]} -> policy_tool_worker"
```
Trace thực tế cho câu `gq01`: `route_reason: retrieval keywords=['p1', 'ticket', 'sla', 'escalation'] -> retrieval_worker`. Latency của bước routing này là 0ms (so với hàng giây nếu dùng LLM).

---

## 3. Kết quả grading questions (150–200 từ)

**Tổng điểm raw ước tính:** 83 / 96

Nhóm chạy pipeline với bộ `grading_questions.json` và đối chiếu kết quả trong `artifacts/grading_run.jsonl`. Với cách chấm an toàn, nhóm đạt khoảng **83/96 raw**, tương đương khoảng **25.94/30** điểm grading sau quy đổi.

**Câu pipeline xử lý tốt nhất:**
- ID: `gq01` — Lý do: Answer nêu đủ 3 kênh notification (`Slack #incident-p1`, `email incident@company.internal`, `PagerDuty`), tính đúng deadline escalation `22:57` (10 phút sau 22:47), và nêu đúng đối tượng escalation là `Senior Engineer`. Trace ghi đúng `retrieval_worker` -> `synthesis_worker`.

**Câu pipeline fail hoặc partial:**
- ID: `gq09` — Partial: Phần SLA P1 đã chính xác, phần Level 2 emergency access cũng đã nêu được emergency bypass và 2 approvers. Tuy nhiên, answer chưa ghi rõ ý "không cần IT Security" (một chi tiết quan trọng trong rubric).
- Root cause: Synthesis worker khi nhận dữ liệu từ `policy_tool_worker` đôi khi bị "trôi" mất các phủ định (negative constraints) nếu prompt synthesis không đủ mạnh.

**Câu gq07 (abstain):** Nhóm xử lý thế nào?
Nhóm xử lý đúng theo hướng anti-hallucination. Pipeline trả lời rằng tài liệu SLA hiện có không nêu mức phạt tài chính cụ thể khi vi phạm P1 resolution time. Việc không bịa ra con số giúp bảo toàn điểm độ tin cậy (faithfulness).

**Câu gq09 (multi-hop khó nhất):** Trace ghi được 2 workers không? Kết quả thế nào?
Không. Trace ghi `3 workers`: `retrieval_worker`, `policy_tool_worker`, và `synthesis_worker`. Mặc dù hệ thống đã ghép được dữ liệu từ `sla_p1_2026.txt` và `access_control_sop.txt`, nhưng do sự hiện diện của synthesis worker nên không đạt bonus "đúng 2 workers".

---

## 4. So sánh Day 08 vs Day 09 — Điều nhóm quan sát được (150–200 từ)

**Metric thay đổi rõ nhất (có số liệu):**
- **Abstain Rate**: Day 08 có abstain rate 10% (1/10), trong khi Day 09 giảm xuống còn 5% (1/21). Điều này cho thấy kiến trúc Multi-agent với các worker chuyên biệt giúp hệ thống "tự tin" hơn khi xử lý các câu hỏi phức tạp nhờ có worker policy kiểm tra chéo.
- **Explainability**: Day 09 cung cấp `route_reason` và trace từng bước, giúp việc debug nhanh hơn gấp 3 lần so với việc phải đọc lại toàn bộ prompt RAG của Day 08.

**Điều nhóm bất ngờ nhất khi chuyển từ single sang multi-agent:**
Khả năng "tự sửa lỗi" (self-correction) gián tiếp. Ví dụ câu `gq02`, Supervisor nhận ra keywords policy nhưng qua worker retrieval không thấy file v3, dẫn đến việc kích hoạt `hitl_triggered=True` và đưa ra câu trả lời cẩn trọng thay vì hallucinate như single agent.

**Trường hợp multi-agent KHÔNG giúp ích hoặc làm chậm hệ thống:**
Đối với các câu hỏi cực kỳ đơn giản (e.g., "Slack channel cho P1 là gì?"), việc đi qua Supervisor -> Retrieval -> Synthesis là một sự lãng phí về latency. Single agent của Day 08 xử lý các câu này nhanh hơn ~20% do không có overhead của orchestration logic.

---

## 5. Phân công và đánh giá nhóm (100–150 từ)

**Phân công thực tế:**
X10 là nhóm chỉ có 1 thành viên nên Nguyễn Tuấn Khanh chịu trách nhiệm 100% tất cả các hạng mục.

| Thành viên | Phần đã làm | Sprint |
|------------|-------------|--------|
| Nguyễn Tuấn Khanh | Implement Graph, Supervisor, Workers, MCP Server, Evaluation | 1, 2, 3, 4 |

**Điều làm tốt:**
- Hoàn thành đầy đủ 4 MCP tools và tích hợp mượt mà vào luồng logic.
- Trace log chi tiết, thể hiện rõ được logic routing và sự tham gia của từng worker.
- Cơ chế Abstain/HITL hoạt động chính xác cho các câu hỏi thiếu dữ liệu (gq02, gq07).

**Điều làm chưa tốt hoặc gặp vấn đề:**
- Synthesis worker đôi khi làm mất chi tiết nhỏ trong các câu multi-hop (như ý "không cần IT Security" ở gq09).
- Latency còn khá cao (~1.3s trung bình) do các worker chạy tuần tự thay vì song song.

---

## 6. Nếu có thêm 1 ngày, nhóm sẽ làm gì? (50–100 từ)
Tôi sẽ nâng cấp `synthesis_worker` thành một **"Verifier Worker"**. Thay vì chỉ đọc evidence rồi viết answer, worker này sẽ đối chiếu answer dự thảo với các constraints từ task (ví dụ: "liệt kê tất cả điều kiện", "không cần ai?"). Bằng chứng từ trace `gq09` cho thấy nếu có bước kiểm chứng này, hệ thống sẽ không bỏ sót ý "không cần IT Security".

---
*File này lưu tại: `reports/group_report.md`*  
*Commit sau 18:00 được phép theo SCORING.md*
