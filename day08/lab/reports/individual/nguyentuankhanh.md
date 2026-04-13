# Báo Cáo Cá Nhân — Lab Day 08: RAG Pipeline

**Họ và tên:** Nguyễn Tuấn Khanh  
**Vai trò trong nhóm:** Tech Lead / Retrieval Owner / Eval Owner / Documentation Owner  
**Ngày nộp:** 2026-04-13  
**Độ dài:** ~750 từ

---

## 1. Tôi đã làm gì trong lab này?

Vì làm lab này một mình nên tôi "ôm" trọn gói từ Sprint 1 đến Sprint 4. Ở giai đoạn đầu, tôi mất khá nhiều thời gian để viết script `index.py`. Thay vì dùng các thư viện chia chunk có sẵn, tôi tự xây dựng logic **Section-first chunking** để đảm bảo máy không bao giờ cắt đôi một điều khoản chính sách quan trọng. 

Sang phần retrieval, tôi không chỉ dùng Dense Search thông thường mà còn mày mò cài thêm BM25 và cơ chế trộn kết quả RRF (Reciprocal Rank Fusion). Để tăng độ chính xác, tôi còn tự tay build một bộ "Source Hint" — kiểu như dạy cho AI biết là nếu khách hỏi về "nghỉ phép" thì phải nhìn vào bộ phận HR trước tiên. Cuối cùng, tôi dành cả đêm để hoàn thiện bộ Evaluator dùng LLM-as-Judge để chấm điểm scorecard, giúp việc đánh giá không còn cảm tính như trước.

---

## 2. Điều tôi hiểu rõ hơn sau lab này (A-ha moments)

Có hai bài học mà nếu chỉ đọc slide tôi sẽ không bao giờ cảm nhận được sâu sắc.

Thứ nhất, **"Garbage in, garbage out"** ở khâu chunking. Trước đây tôi nghĩ cứ embedding model xịn là sẽ tìm đúng. Nhưng thực tế cho thấy, nếu bạn chia chunk không "khéo" (cắt ngang một câu quan trọng hoặc mất metadata về ngày hiệu lực), thì model dù thông minh đến đâu cũng sẽ trả lời sai. Tôi nhận ra metadata không chỉ để hiển thị cho đẹp, mà nó chính là "la bàn" để retriever không bị lạc trong đống tài liệu.

Thứ hai là về **Grounded Prompting**. Tôi nhận ra prompt không nên là một yêu cầu viết tự do, mà nên là một cái khung sắt. Việc ép model trả về JSON structured giúp tôi kiểm soát được model: nó phải liệt kê rõ "Ngoại lệ là gì?", "Điều kiện là gì?". Nếu nó không tìm thấy, nó phải để trống thay vì cố "sáng tác" ra một câu trả lời nghe có vẻ hợp lý nhưng lại hoàn toàn sai sự thật.

---

## 3. Điều tôi ngạc nhiên hoặc gặp khó khăn

Điểm làm tôi bất ngờ nhất chính là việc **Hybrid Retrieval không phải lúc nào cũng tốt hơn Dense**. Lúc đầu tôi rất tự tin là khi kết hợp cả keyword search và semantic search thì điểm recall sẽ tăng vọt. Nhưng kết quả thực tế từ scorecard cho thấy bộ Hybrid của tôi lại làm giảm điểm Completeness. Nó làm tôi nhận ra một chân lý trong RAG: đôi khi "quá nhiều thông tin là không có thông tin". Việc lấy về quá nhiều chunk chứa từ khóa trùng lặp nhưng nghèo nàn về ngữ nghĩa lại vô tình làm lu mờ đi chunk chứa câu trả lời chuẩn xác nhất.

Khó khăn lớn nhất là lúc debug cái "ngã tư" giữa Retrieval và Generation. Có những câu retriever đã tìm đúng tài liệu rồi, nhưng model vẫn trả lời thiếu ý. Việc xác định xem lỗi nằm ở đâu — do chunking chưa đủ thông tin hay do prompt chưa đủ ép phê — là công việc cực kỳ tốn nơ-ron và mất nhiều thời gian nhất.

---

## 4. Phân tích một câu hỏi trong scorecard

**Câu hỏi:** Nếu cần hoàn tiền khẩn cấp cho khách hàng VIP, quy trình có khác không? (`q10`)

**Phân tích Chi tiết:**
Đây là câu hỏi mà tôi tâm đắc nhất vì nó phơi bày sự khác biệt giữa "đúng" và "đúng nhưng chưa đủ". Cả hai cấu hình hệ thống của tôi đều đạt điểm Faithfulness tuyệt đối (không bịa), nhưng điểm Completeness lại lẹt đẹt ở mức 3/5.

Tại sao thế? Khi soi vào `grading_run.json`, tôi thấy retriever đã lấy về đúng trang chính sách hoàn tiền (`policy/refund-v4.pdf`). Tài liệu không hề có mục nào dành riêng cho khách hàng VIP. Model của tôi thấy vậy thì chỉ trả lời: *"Tài liệu không đề cập đến quy định riêng cho VIP"*. 

Về mặt kỹ thuật, nó trả lời đúng. Nhưng về mặt nghiệp vụ, nó chưa đạt. Một câu trả lời hoàn hảo phải là: *"Chính sách không có quy định riêng cho VIP, vì vậy trường hợp này vẫn áp dụng quy chuẩn chung..."*. Lỗi này giúp tôi hiểu rằng pipeline của mình còn thiếu một bước **Logic Reasoning** — khả năng nối kết giữa việc "không có ngoại lệ" và "áp dụng luật chung". Đây là điểm mà tôi sẽ phải cải thiện ở prompt trong phiên bản tiếp theo.

---

## 5. Nếu có thêm thời gian, tôi sẽ làm gì?

Nếu còn thêm thời gian, tôi sẽ vứt bỏ cách rescore bằng heuristic (source hint tự viết) để chuyển sang dùng **Rerank Cross-Encoder**. Tôi muốn dùng một model nhỏ (như BGE-Reranker) để nó tự chấm lại điểm cho top-10 chunk thay vì mình tự đoán. Ngoài ra, tôi muốn triển khai **Metadata Filtering** cứng: nếu khách hỏi về chính sách năm 2026, hệ thống phải tự động gạt bỏ mọi chunk có date cũ hơn ngay từ vòng gửi xe. Hai cải tiến này dựa trực tiếp trên những vấp váp thực tế mà tôi gặp phải khi phân tích scorecard `q10` và các câu temporal case.

