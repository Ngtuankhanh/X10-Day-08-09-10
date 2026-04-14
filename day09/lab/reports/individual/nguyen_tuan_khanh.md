# Báo Cáo Cá Nhân — Lab Day 09: Multi-Agent Orchestration

**Họ và tên:** Nguyễn Tuấn Khanh  
**Vai trò trong nhóm:** Full-stack AI Engineer / Owner (Chịu trách nhiệm 100% pipeline)  
**Ngày nộp:** 2026-04-14  
**Độ dài yêu cầu:** 500–800 từ

---

## 1. Tôi phụ trách phần nào? (100–150 từ)

Vì nhóm X10 chỉ có mình tôi, nên tôi chịu trách nhiệm thiết kế và triển khai toàn bộ hệ thống từ đầu đến cuối. Cụ thể:
- **Supervisor & Graph Control**: Tôi xây dựng file `graph.py` sử dụng mô hình Supervisor-Worker, triển khai logic routing dựa trên từ khóa và phân tích rủi ro.
- **Worker Development**: Tôi viết 3 workers chính (`retrieval.py`, `policy_tool.py`, `synthesis.py`) và định nghĩa contract giao tiếp giữa chúng thông qua `AgentState`.
- **MCP Server**: Tôi tự thiết kế `mcp_server.py` để mô phỏng giao thức Model Context Protocol, cung cấp các công cụ như tra cứu ticket thực tế và kiểm tra quyền truy cập (Access Control).
- **Evaluation & Tracing**: Tôi viết script `eval_trace.py` để tự động hóa việc chạy 21 câu hỏi thử nghiệm, thu thập metrics về latency, confidence và lưu vết (trace) dưới dạng JSON trong thư mục `artifacts/traces`.

---

## 2. Tôi đã ra một quyết định kỹ thuật gì? (150–200 từ)

**Quyết định:** Sử dụng Keyword-based Routing kết hợp với `route_reason` tường minh thay vì dùng LLM-based Classification cho Supervisor.

**Lý do:**
Khi bắt đầu Sprint 1, tôi đã thử dùng một LLM (GPT-4o-mini) để phân loại task vào các worker. Tuy nhiên, tôi nhận thấy hai nhược điểm lớn:
1. **Latency**: Mỗi request tốn thêm trung bình 1.1 giây chỉ để quyết định route.
2. **Predictability**: Một số câu hỏi multi-hop như `gq09` (hỏi cả về SLA và Access) khiến LLM bối rối và đôi khi chỉ chọn một trong hai worker, dẫn đến thiếu hụt context.
Bằng cách dùng Keyword-based routing, tôi có thể ép hệ thống route sang `policy_tool_worker` (vốn đã bao gồm cả retrieval bên trong) khi thấy các từ khóa nhạy cảm như "access", "level". Điều này giúp latency bước routing giảm xuống gần bằng 0ms và kết quả routing là hoàn toàn có thể dự đoán được (deterministic).

**Trade-off đã chấp nhận:**
Hệ thống sẽ kém linh hoạt hơn với các câu hỏi sử dụng ngôn ngữ quá tự nhiên hoặc không chứa từ khóa định danh. Để khắc phục, tôi đã thêm một "default route" dẫn đến `retrieval_worker` để đảm bảo không câu hỏi nào bị bỏ rơi.

**Bằng chứng từ trace/code:**
Trong `graph.py`:
```python
if policy_matches:
    route = "policy_tool_worker"
    needs_tool = True
    route_reason = f"policy/access keywords={policy_matches[:4]} -> policy_tool_worker"
```
Trace của câu `gq03` (Engineer cần Level 3 access):
- `supervisor_route`: "policy_tool_worker"
- `route_reason`: "policy/access keywords=['access', 'level 3'] -> policy_tool_worker"
- `latency_ms`: 4 (cực nhanh nhờ xử lý local)

---

## 3. Tôi đã sửa một lỗi gì? (150–200 từ)

**Lỗi:** `retrieval_worker` bỏ sót dữ liệu quan trọng trong các câu hỏi Multi-hop do giới hạn `top_k` mặc định.

**Symptom:** 
Khi chạy thử câu `gq09` (kết hợp SLA P1 và Level 2 Access), pipeline chỉ trả lời đúng phần SLA nhưng lại nói "không đủ thông tin" cho phần Access Control, mặc dù file `access_control_sop.txt` đã được load.

**Root cause:**
Trong hàm `run` của `workers/retrieval.py`, tôi đã hard-code `top_k=3` khi gọi `retrieve_dense`. Tuy nhiên, Supervisor trong `graph.py` đã gửi yêu cầu nâng cấp lên `top_k=10` cho các task policy nhưng worker lại không đọc giá trị này từ `state`. Kết quả là các chunks liên quan đến Level 2 Access (nằm ở cuối file SOP) bị xếp hạng thấp hơn các chunks SLA và bị cắt bỏ.

**Cách sửa:**
Tôi đã cập nhật worker retrieval để ưu tiên lấy `top_k` từ `state` do Supervisor yêu cầu trước khi dùng giá trị mặc định.

**Bằng chứng trước/sau:**
*Trước khi sửa (Code):*
```python
# workers/retrieval.py
chunks = retrieve_dense(task, top_k=3) # Lỗi: luôn là 3
```
*Sau khi sửa (Code):*
```python
# workers/retrieval.py (Line 472)
top_k = state.get("top_k", DEFAULT_TOP_K)
chunks = retrieve_dense(task, top_k=top_k)
```
*Kết quả sau khi sửa:* Trace `gq09` cho thấy `retrieved_chunks` tăng lên 10, bao gồm đầy đủ cả info về `sla_p1_2026.txt` và `access_control_sop.txt`, giúp Synthesis đưa ra câu trả lời multi-hop chính xác.

---

## 4. Tôi tự đánh giá đóng góp của mình (100–150 từ)

**Tôi làm tốt nhất ở điểm nào?**
Tôi đã xây dựng được một hệ thống Trace cực kỳ chi tiết. Mọi quyết định của Supervisor từ lý do route (`route_reason`), việc có kích hoạt HITL hay không, đến việc gọi những công cụ MCP nào đều được ghi lại. Điều này không chỉ giúp hoàn thành yêu cầu lab mà còn giúp tôi debug cực nhanh khi pipeline trả lời sai.

**Tôi làm chưa tốt hoặc còn yếu ở điểm nào?**
Tôi chưa tối ưu được prompt cho `synthesis_worker`. Trong các câu hỏi cần độ chính xác tuyệt đối như "phải nêu rõ không cần ai phê duyệt" (`gq09`), synthesis đôi khi tóm tắt quá đà dẫn đến mất đi những chi tiết mang tính "negative constraint".

**Nhóm phụ thuộc vào tôi ở đâu?**
Vì làm một mình, tôi là người nắm giữ toàn bộ logic kết nối (Graph). Nếu tôi không định nghĩa đúng `AgentState` ban đầu, các worker sẽ không thể truyền dữ liệu cho nhau.

---

## 5. Nếu có thêm 2 giờ, tôi sẽ làm gì? (50–100 từ)

Tôi sẽ nâng cấp `synthesis_worker` để hỗ trợ **"Strict Schema Enforcement"**. Tôi nhận thấy trace của câu `gq09` bị mất điểm vì không nêu rõ ý "không cần IT Security". Nếu có thêm 2 giờ, tôi sẽ dùng kỹ thuật "Chain-of-Verification" để synthesis tự soát lại câu trả lời dựa trên bộ tiêu chí (rubric) trích xuất từ câu hỏi gốc.