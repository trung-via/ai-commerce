# BQ-04a Offline Engineering Handoff (R4 Brain Hold Repair)

**Gói công việc:** BQ-04a (Q5/R7) Targeted Media Repair, Preservation, and Reinspection  
**Lần thử nghiệm:** R4 (`BQ04A-Q5-R7-BRAIN-HOLD-REPAIR-AGY-04-bq04a_brain_hold_repair_20261106_r4_agy`)  
**Commit cơ sở (Source SHA):** `3f8715d8020df646d646a527c012c9695be9bc64` (Ứng viên R3 chưa merge, branch từ main commit `158cd193b18315374c29848b5697641853304959`)  
**Môi trường thực thi:** Windows-11 Self-Hosted Runner (`Python 3.14.7`, `pytest-9.2.0`, `xdist-3.8.0`)  
**Executor / Model:** Antigravity Gemini 3.8 Flash High (`antigravity / gemini-3.8-flash / effort high`)  
**Hành động Git:** SOURCE-ONLY REPAIR (Chỉ chỉnh sửa 8 tệp cho phép, tuyệt đối không chạy git write / commit / branch / push, worker EF02 độc lập nghiệm thu)

---

## 1. Mục Tiêu Sửa Lỗi Brain Hold (Issue #490 Resolution)

Lần chạy R4 sửa chữa triệt để 3 khiếm khuyết logic được phát hiện trong đợt đánh giá Brain Hold (Issue #490) từ ứng viên R3:

1. **Loại Bỏ Thăng Hạng Lỗi Người Gọi Chưa Xác Minh (Untrusted Caller Defect Promotion):**
   - Trước đây, `context["grounded_defect"]` do người gọi tự khai báo có thể kích hoạt `PROPOSE_LOCAL_DETERMINISTIC_EDIT`.
   - Trong R4, khiếm khuyết của người gọi không kèm đo lường gốc của công cụ Q4 (`measured_findings` hoặc `technical_evidence.findings`) bắt buộc chuyển về `RETURN_TO_BRAIN` với lý do `UNPROVEN_CALLER_DEFECT_NO_NATIVE_OBSERVATION`.
   - `PROPOSE_LOCAL_DETERMINISTIC_EDIT` chỉ được đề xuất khi có phát hiện khiếm khuyết thực tế được chứng thực bởi công cụ Q4.

2. **Chặn Yêu Cầu Tái Kiểm Định Không Khả Thi (Non-Actionable HOLD Request Fail-Closed):**
   - Trước đây, kế hoạch bảo toàn ở trạng thái `HOLD` vẫn có thể sinh yêu cầu tái kiểm định thông thường dẫn đến đánh giá closure.
   - Trong R4, `request_reinspection` trên kế hoạch `HOLD` hoặc `RETURN_TO_BRAIN` fail-closed ngay lập tức thành trạng thái không khả thi (`HOLD_...`, `RETURNED_TO_BRAIN_NON_ACTIONABLE`).
   - `evaluate_repair_closure` từ chối các yêu cầu này với `HOLD_REINSPECTION_BLOCKED` và `outcome="NON_ACTIONABLE_REQUEST"`.

3. **Loại Bỏ Khẳng Định Cải Thiện Giả Tạo (Eliminated False Improvement / Preservation Assertions):**
   - Trong BQ-04a (thuần túy logic ngoại tuyến, chưa có đo lường trực tiếp có xác thực của BQ-04b), việc cung cấp media thay thế sạch hay media giả lập có SHA khác **không cấu thành bằng chứng sửa chữa thành công**.
   - Trạng thái mặc định luôn là `invariants_unregressed=False`, `target_corrected=False`, `status="IMPROVEMENT_UNPROVEN"`, `outcome="IMPROVEMENT_UNPROVEN"`.
   - Replay `from_dict`/`from_json` đối chiếu toàn bộ các trường với bản ghi sinh lại từ nguồn tin cậy, từ chối mọi trường bị sửa đổi, thiếu hoặc tiêm thêm trái phép.

---

## 2. Danh Sách 8 Tệp Thuộc Phạm Vi Cho Phép (Exact Eight Allowed Paths)

Toàn bộ các chỉnh sửa trong R4 chỉ nằm trong đúng 8 tệp được phân công:

1. `src/ai_commerce/creative_quality/repair_policy.py`: Chính sách ra quyết định sửa chữa tất định có căn cứ (`BQ04aRepairDecision.v1`), fail-closed đối với P0, yêu cầu quan sát kỹ thuật gốc từ Q4 trước khi đề xuất local edit, trả về Brain khi lỗi chưa được chứng thực.
2. `src/ai_commerce/creative_quality/repair_preservation.py`: Kế hoạch bảo toàn bất biến (`BQ04aPreservationPlan.v1`), khóa chặt các bất biến không mục tiêu, fail-closed khi thiếu oracle hoặc quyền, xác thực replay toàn diện.
3. `src/ai_commerce/creative_quality/repair_reinspection.py`: Yêu cầu tái kiểm định Q4 (`BQ04aReinspectionRequest.v1`) và bản đối soát đóng (`BQ04aRepairClosure.v1`), chặn tái kiểm định khi kế hoạch bị HOLD/Return-to-Brain, từ chối cải thiện giả lập/chưa đo lường thực tế, replay an toàn tuyệt đối.
4. `tests/test_bq_04a_targeted_repair.py`: Bộ kiểm thử 152 ca kiểm thử phân luồng, bao phủ ma trận replay tamper đầy đủ cho 4 loại bản ghi, ma trận editor/probes/receipts, và kiểm tra không có tác dụng phụ ngoại vi.
5. `work_items/mt01-components/bq-04a.json`: Bản kê đăng ký thành phần MT-01 (`MT01ComponentOwner.v1`) khai báo quyền sở hữu 3 file nguồn, 1 file test, 3 mã AC, và liên kết đóng với các consumer.
6. `work_items/BQ-04A_TARGETED_REPAIR_001.json`: WorkItem V1 ghi nhận đầy đủ siêu dữ liệu R4, lịch sử R1/R2/R3, và 2 vòng kiểm định verify-fix.
7. `docs/engineering/BQ_04A_OFFLINE_HANDOFF.md`: Báo cáo bàn giao kỹ thuật ngoại tuyến cho R4.
8. `docs/creative/BQ_04A_TARGETED_REPAIR_PROTOCOL.md`: Giao thức nghiệp vụ sáng tạo Q5/R7 thân thiện bằng tiếng Việt cập nhật các nguyên tắc mới về unproven improvement và caller defect verification.

---

## 3. Kết Quả Xác Minh MT-01 V2.1 & Pytest-Xdist (12 Workers)

### Xác thực Manifest đăng ký Onboarding (`mt01-component-onboarding`):
- Lệnh chạy: `python -m pytest tests/test_mt01_v21.py -q -n 12`
- Kết quả: **60 passed, 12 warnings in 4.13s** trên 12 workers song song.

### Lựa chọn nhóm chịu ảnh hưởng qua thuật toán `registered_action_plan`:
- **Selected Groups:** `['a13', 'bq-02a', 'bq-02b', 'bq-03b', 'bq-04a']`
- **Reason Codes:** `['ONBOARD_CONSUMER_OWNER_CLOSURE']`
- **Fallback to Full Suite:** `False`
- **Unknown ACs:** `[]`

### Kết quả kiểm thử từng nhóm:

| Nhóm (Group) | Lệnh Thực Thi Pytest | Số Ca Đạt | Lỗi / Thất Bại | Số Worker | Thời Gian |
|---|---|---|---|---|---|
| **bq-04a** | `python -m pytest tests/test_bq_04a_targeted_repair.py -q -n 12` | **152** | 0 | 12 | 9.97s |
| **bq-02a** | `python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12` | **282** | 0 | 12 | 15.08s |
| **bq-02b** | `python -m pytest tests/test_bq_02b_multimodal.py -q -n 12` | **249** | 0 | 12 | 15.67s |
| **bq-03b** | `python -m pytest tests/test_bq_03b_human_study_readiness.py -q -n 12` | **298** | 0 | 12 | 2.74s |
| **a13** | `python -m pytest tests/test_a13_media_inspection.py -q -n 12` | **39** | 0 | 12 | 5.74s |
| **mt01-onboarding** | `python -m pytest tests/test_mt01_v21.py -q -n 12` | **60** | 0 | 12 | 4.13s |
| **TỔNG CỘNG** | **Toàn bộ các nhóm kiểm định liên quan** | **1080** | **0** | **12** | **53.33s** |

---

## 4. Lịch Sử Các Vòng Kiểm Định R4 & Lưu Giữ Bằng Chứng Thất Bại

Hệ thống tuân thủ giới hạn tối đa 2 vòng kiểm định sửa lỗi (`verification_round_limit: 2`):

- **Vòng 1 (Round 1):**
  - Chạy `pytest tests/test_bq_04a_targeted_repair.py` sau khi mở rộng các ma trận tamper replay và negative test cases.
  - Kết quả: **144 passed, 8 failed** (exit code = 1).
  - Các lỗi được lưu giữ và phân tích trung thực:
    1. `test_reinspection_request_replay_tamper_matrix[expected_sha]`: `from_dict` tin tưởng `expected_derived_sha256` truyền vào trong dict thay vì đối chiếu với plan.
    2. `test_reinspection_request_replay_tamper_matrix[probes]`: `from_dict` tin tưởng `expected_probes` truyền vào trong dict thay vì cấu hình mặc định.
    3. `test_reinspection_request_replay_tamper_matrix[injected_extra]` & `test_closure_replay_tamper_matrix[injected_extra]`: Thông báo ngoại lệ `unexpected, extra, or missing fields` khác regex mong đợi `replay mismatch`.
    4. `test_hold_preservation_plan_blocks_actionable_reinspection`: Mã lý do thực tế là `HOLD_PRESERVATION_CANNOT_REQUEST_REINSPECTION`.
    5. `test_returned_to_brain_plan_blocks_actionable_reinspection`: Mã lý do thực tế là `RETURNED_TO_BRAIN_NO_REINSPECTION`.
    6. `test_bad_expected_derived_sha_closure`: Mã lý do thực tế là `EXPECTED_AFTER_SHA_MISMATCH`.
    7. `test_preservation_replay_with_context_and_roundtrip`: Context thử nghiệm không mang tính ngữ nghĩa; đã chuyển sang kiểm thử context `missing_rights: True`.
- **Sửa chữa & Vòng 2 (Round 2):**
  - Khóa chặt `expected_derived_sha256` và `expected_probes` trong `BQ04aReinspectionRequest.from_dict` theo mặc định của plan.
  - Chuẩn hóa các câu lệnh assert theo đúng mã lý do kỹ thuật.
  - Cập nhật test ngữ nghĩa với context quyền.
  - Kết quả: **152 passed, 0 failed** trên `bq-04a`, 100% kiểm thử xanh trên toàn bộ 6 bộ kiểm thử liên quan.

---

## 5. Phân Tách Ranh Giới Thẩm Quyền (Authority & Boundary Separation)

1. **Nhật Ký Thực Thi vs Chứng Từ Độc Lập:** Nhật ký chạy lệnh của Executor chỉ là bằng chứng nội bộ; việc nghiệm thu và công bố ứng viên hoàn toàn thuộc quyền kiểm chứng độc lập của worker EF02.
2. **Không Có Tác Động Media Thực Tế:** BQ-04a không chỉnh sửa bất kỳ frame video hay âm thanh nào, không giải mã trực tiếp ffmpeg trong code production, không gọi mô hình AI inpainting/generative.
3. **Không Tự Động Xuất Bản:** Điểm kiểm định xanh (`1080 passed`) chỉ chứng minh tính đúng đắn logic của mã nguồn phần mềm, **tuyệt đối không tương đương với phê duyệt phát hành (`GO_PUBLISH`) hoặc chi tiêu tài chính (`SPEND_AUTHORIZATION`)**.
4. **Không Thay Đổi Hợp Đồng Đóng:** Các schema cốt lõi `CreativeDirective.v1`, `BrainDecision.v1`, `ProductJourneyReceipt.v1` và legacy 63 WBS được bảo toàn tuyệt đối không suy suyển.
