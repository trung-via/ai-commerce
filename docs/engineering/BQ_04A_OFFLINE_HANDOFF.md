# BQ-04a Offline Engineering Handoff

**Gói công việc:** BQ-04a (Q5/R7) Targeted Media Repair, Preservation, and Reinspection  
**Commit cơ sở (Source SHA):** `158cd193b18315374c29848b5697641853304959`  
**Môi trường thực thi:** Windows-11 Self-Hosted Runner (`Python 3.14.7`, `pytest-9.2.0`, `xdist-3.8.0`)  
**Executor / Model:** Antigravity Gemini 3.8 Flash High (`antigravity / gemini-3.8-flash / effort high`)  
**Hành động Git:** ADD-ONLY (8 tệp mới, không sửa đổi tệp hiện hữu, không ghi Git HEAD/ref)

---

## 1. Danh Sách 8 Tệp Bổ Sung (Exact Eight ADD-Only Scope)

Tất cả 8 tệp đều được kiểm tra vắng mặt trước khi tạo (absent at entry) và được tạo mới hoàn toàn:

1. `src/ai_commerce/creative_quality/repair_policy.py`: Chính sách ra quyết định sửa chữa tất định có căn cứ (`BQ04aRepairDecision.v1`), phân tách T1/T2 thật khỏi T3 chưa chạy và ngữ nghĩa UNKNOWN, fail-closed đối với P0 (quyền, sự thật sản phẩm, danh tính), miễn trừ cho sản phẩm null/âm thanh im lặng/hoạt họa giả tưởng, 5 lựa chọn khép kín (`KEEP_ORIGINAL`, `PROPOSE_LOCAL_DETERMINISTIC_EDIT`, `PROPOSE_SHOT_REGENERATION`, `RETURN_TO_BRAIN`, `HOLD`).
2. `src/ai_commerce/creative_quality/repair_preservation.py`: Kế hoạch bảo toàn bất biến ngoài mục tiêu (`BQ04aPreservationPlan.v1`), khóa chặt danh tính sản phẩm, quyền hình ảnh/giọng nói, tính liên tục âm thanh/thời gian/nhân quả, giới hạn phạm vi không gian/thời gian cụ thể, không bịa đặt hash SHA đầu ra.
3. `src/ai_commerce/creative_quality/repair_reinspection.py`: Yêu cầu tái kiểm định đối chứng Q4 (`BQ04aReinspectionRequest.v1`) và bản đối soát đóng sửa đổi (`BQ04aRepairClosure.v1`), loại bỏ bằng chứng giả lập khỏi quan sát thực, xác thực byte mới sau sửa chữa, không tự động cấp PASS hoặc giấy phép xuất bản.
4. `tests/test_bq_04a_targeted_repair.py`: Bộ kiểm thử ngoại tuyến tất định gồm 107 ca kiểm thử phân luồng, bao phủ toàn bộ 5 hành động, ma trận rủi ro P0/P1/P2/P3, bất biến bảo toàn, ma trận tamper replay, ma trận đầu vào độc hại, và ma trận biên JSON.
5. `work_items/mt01-components/bq-04a.json`: Bản kê đăng ký thành phần MT-01 (`MT01ComponentOwner.v1`) khai báo quyền sở hữu 3 file nguồn mới, 1 file test mới, 3 mã AC, và liên kết đóng với các consumer hiện hữu (`multimodal_review.py`, `quality_review.py`, `t2_sampling.py`, `t3_assessment.py`, `study_readiness.py`, `inspection.py`).
6. `work_items/BQ-04A_TARGETED_REPAIR_001.json`: WorkItem V1 ghi nhận đầy đủ siêu dữ liệu R3, lịch sử các lần chạy trước, các vòng kiểm định cùng bằng chứng thất bại được bảo lưu, và kết quả thực thi MT-01.
7. `docs/engineering/BQ_04A_OFFLINE_HANDOFF.md`: Báo cáo bàn giao kỹ thuật ngoại tuyến, số liệu xdist 12 workers, lý do chọn nhóm MT-01 và ranh giới thẩm quyền.
8. `docs/creative/BQ_04A_TARGETED_REPAIR_PROTOCOL.md`: Giao thức nghiệp vụ sáng tạo Q5/R7 thân thiện với con người bằng tiếng Việt, quy định quy trình 7 bước, phân loại khiếm khuyết, ranh giới BQ-04a/BQ-04b, và các giả thuyết chi phí/độ trễ chưa được kiểm chứng.

---

## 2. Kết Quả Xác Minh MT-01 V2.1 & Pytest-Xdist (12 Workers)

### Xác thực Manifest đăng ký Onboarding (`mt01-component-onboarding`):
- Lệnh chạy: `pytest tests/test_mt01_v21.py -q -n 12`
- Kết quả: **60 passed in 5.69s** trên 12 workers song song.

### Lựa chọn nhóm chịu ảnh hưởng qua thuật toán `registered_action_plan`:
- **Selected Groups:** `['a13', 'bq-02a', 'bq-02b', 'bq-03b', 'bq-04a']`
- **Reason Codes:** `['ONBOARD_CONSUMER_OWNER_CLOSURE']`
- **Fallback to Full Suite:** `False` (Không bị fallback, không kích hoạt kiểm thử toàn bộ dự án)
- **Unknown ACs:** `[]` (Toàn bộ 3 AC đều được nhận diện và gắn kết thành công)

### Kết quả kiểm thử từng nhóm được chọn:

| Nhóm (Group) | Lệnh Thực Thi Pytest | Số Ca Đạt | Lỗi / Thất Bại | Số Worker Thực Tế | Thời Gian |
|---|---|---|---|---|---|
| **bq-04a** | `python -m pytest tests/test_bq_04a_targeted_repair.py -q -n 12` | **107** | 0 | 12 | 4.57s |
| **bq-02a** | `python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12` | **282** | 0 | 12 | 11.58s |
| **bq-02b** | `python -m pytest tests/test_bq_02b_multimodal.py -q -n 12` | **249** | 0 | 12 | 11.57s |
| **bq-03b** | `python -m pytest tests/test_bq_03b_human_study_readiness.py -q -n 12` | **298** | 0 | 12 | 2.14s |
| **a13** | `python -m pytest tests/test_a13_media_inspection.py tests/test_a13_claim_rights.py tests/test_a13_link.py tests/test_a13_evidence_pack.py -q -n 12` | **197** | 0 | 12 | 8.07s |
| **TỔNG CỘNG** | **5 nhóm được chọn theo quan hệ Owner + Consumer Closure** | **1133** | **0** | **12** | **37.93s** |

---

## 3. Lịch Sử Các Vòng Kiểm Định & Lưu Giữ Bằng Chứng Thất Bại (Verify-Fix Retention)

Hệ thống tuân thủ giới hạn tối đa 2 vòng kiểm định sửa lỗi (`verification_round_limit: 2`) và bảo lưu trung thực các lỗi xuất hiện ở vòng 1:

- **Vòng 1 (Round 1):**
  - Chạy `pytest tests/test_bq_04a_targeted_repair.py` (72 ca kiểm thử ban đầu).
  - Kết quả: **70 passed, 2 failed** (exit code = 1).
  - Lỗi 1: `AttributeError: module 'study_protocol' has no attribute 'draft_protocol'`. Nguyên nhân: sử dụng sai tên hàm API của BQ-03b (`draft_protocol` thay vì `build_protocol` và `build_readiness`).
  - Lỗi 2: `AssertionError: assert 'IMPROVEMENT_UNPROVEN' == 'TECHNICAL_TARGET_CORRECTED_INVARIANTS_UNREGRESSED'`. Nguyên nhân: hàm fixture `make_mp4` sinh file MP4 tất định cùng tham số tạo ra SHA byte trùng khớp 100% với file gốc, kích hoạt đúng quy tắc phát hiện `UNCHANGED_ORIGINAL_BYTES`.
- **Sửa chữa & Vòng 2 (Round 2):**
  - Cập nhật hàm gọi `sp.build_protocol()` và `sr.build_readiness()` chuẩn xác.
  - Cập nhật tham số tạo media sau sửa đổi (`segmented=False`, tên file khác) để tạo ra byte mới độc lập.
  - Bổ sung các ma trận kiểm thử mở rộng (editor matrix, probe matrix, replay tamper matrix) nâng tổng số ca kiểm thử lên 107.
  - Kết quả: **107 passed, 0 failed** trên `bq-04a`, và toàn bộ **1133 passed, 0 failed** trên toàn bộ 5 nhóm liên quan của MT-01.

---

## 4. Phân Tách Ranh Giới Thẩm Quyền (Authority & Boundary Separation)

1. **Nhật Ký Thực Thi vs Chứng Từ Độc Lập:** Nhật ký chạy lệnh của Agent / Executor trong phiên này chỉ là bằng chứng nội bộ; việc nghiệm thu và công bố ứng viên hoàn toàn thuộc quyền kiểm chứng độc lập của worker EF02.
2. **Không Có Tác Động Media Thực Tế:** BQ-04a không chỉnh sửa bất kỳ frame video hay âm thanh nào, không giải mã trực tiếp ffmpeg trong code production, không gọi mô hình AI inpainting/generative.
3. **Không Tự Động Xuất Bản:** Điểm kiểm định xanh (`1133 passed`) chỉ chứng minh tính đúng đắn logic của mã nguồn phần mềm, **tuyệt đối không tương đương với phê duyệt phát hành (`GO_PUBLISH`) hoặc chi tiêu tài chính (`SPEND_AUTHORIZATION`)**.
4. **Không Thay Đổi Hợp Đồng Đóng:** Các schema cốt lõi `CreativeDirective.v1`, `BrainDecision.v1`, `ProductJourneyReceipt.v1` và legacy 63 WBS được bảo toàn tuyệt đối không suy suyển.
