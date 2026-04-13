# Tuning Log — RAG Pipeline (Day 08 Lab)

> Nhật ký ghi lại quá trình tối ưu hóa hệ thống bằng phương pháp A/B testing.

---

## Baseline (Sprint 2)

**Ngày:** 2026-04-13  
**Config:**
```python
retrieval_mode = "dense"
chunk_size = 400 tokens
overlap = 80 tokens
top_k_search = 10
top_k_select = 3
use_rerank = False
llm_model = "gpt-4o-mini"
```

**Scorecard Baseline:**
| Metric | Average Score |
|--------|--------------|
| Faithfulness | 5.00 /5 |
| Answer Relevance | 4.80 /5 |
| Context Recall | 4.78 /5 |
| Completeness | 4.80 /5 |

**Câu hỏi yếu nhất (điểm thấp):**
- **q10 (Refund Policy Exception)**: Completeness = 3/5. Model bỏ lỡ ý quan trọng về việc tất cả quy trình hoàn tiền đều tuân theo quy trình chuẩn của công ty.
- **q01 (Technical SLA)**: Completeness = 3/5. Thiếu thông tin chi tiết về các mốc thời gian phụ dù đã lấy đúng source.

**Giả thuyết nguyên nhân (Error Tree):**
- [ ] Retrieval: Dense bỏ lỡ exact keyword cho các mã tài liệu (SOP-01).
- [x] Generation: Prompt chưa nhấn mạnh việc trích xuất "Standard Rule" khi người dùng hỏi về trường hợp đặc biệt.
- [ ] Retrieval: Top-k 3 đôi khi quá hẹp để chứa cả điều khoản đặc biệt và điều khoản chung.

---

## Variant 1 (Sprint 3)

**Ngày:** 2026-04-13  
**Biến thay đổi:** `retrieval_mode = "hybrid"`  
**Lý do chọn biến này:**
Dựa trên quan sát ở baseline, Dense retrieval đôi khi mang về các chunk có ngữ nghĩa tương đồng nhưng thiếu keywords quan trọng như tên SOP hoặc mã SLAs cụ thể. Hybrid (Dense + Sparse) giúp đảm bảo các câu hỏi chứa keyword kỹ thuật được ưu tiên cao hơn nhờ trọng số BM25.

**Config thay đổi:**
```python
retrieval_mode = "hybrid"
# Các tham số còn lại giữ nguyên như baseline để đảm bảo A/B testing chuẩn
```

**Scorecard Variant 1:**
| Metric | Baseline | Variant 1 | Delta |
|--------|----------|-----------|-------|
| Faithfulness | 5.00/5 | 5.00/5 | +0.00 |
| Answer Relevance | 4.80/5 | 4.80/5 | +0.00 |
| Context Recall | 4.78/5 | 4.78/5 | +0.00 |
| Completeness | 4.80/5 | 4.60/5 | -0.20 |

**Nhận xét:**
- **Ưu điểm**: Faithfulness duy trì tuyệt đối ở mức 5.0/5, chứng tỏ hệ thống cực kỳ an toàn, không sinh ra thông tin sai lệch.
- **Vấn đề**: Variant 1 (Hybrid) thực tế lại có điểm Completeness thấp hơn Baseline (-0.20). Điều này xảy ra do Reciprocal Rank Fusion (RRF) đôi khi đẩy các chunk có keyword trùng lặp mạnh nhưng ít ngữ nghĩa giải thích lên trên, khiến context gửi vào LLM bị loãng hơn so với Dense đơn thuần.
- **Câu hỏi cụ thể**: `q10` vẫn là điểm yếu chung của cả hai cấu hình, cần cải thiện logic xử lý "special case vs standard rule".

**Kết luận:**
Hiện tại, **Baseline (Dense retrieval)** vẫn là cấu hình ổn định hơn cho bộ test case này. Tuy nhiên, việc duy trì **Faithfulness 5/5** là thành công lớn nhất, đảm bảo tính ứng dụng cao trong môi trường doanh nghiệp khắt khe về độ chính xác.

---

## Tóm tắt học được

1. **Lỗi phổ biến nhất trong pipeline này là gì?**
   Lỗi Completeness (thiếu thông tin). Model đã lấy đúng tài liệu nhưng đôi khi bỏ sót các điều kiện chuẩn (standard rules) khi quá tập trung vào điều kiện đặc biệt mà người dùng hỏi.

2. **Biến nào có tác động lớn nhất tới chất lượng?**
   Prompt Engineering (Grounded structured prompt) giúp đạt độ trung thực cao. Retrieval mode (Hybrid vs Dense) có tác động rõ rệt tới thứ tự chunk nhưng cần tune thêm trọng số alpha.

3. **Nếu có thêm 1 giờ, nhóm sẽ thử gì tiếp theo?**
   - Triển khai **Rerank (Cross-Encoder)** để lọc top-10 xuống top-3 thay vì dùng rescoring heuristic.
   - Thử nghiệm **Recursive Character Chunking** nhỏ hơn (200-300 tokens) để tăng độ linh hoạt trong việc ghép context.
