# Work Tree B-QUALITY-001 v1.0 — CIMQ + CRI R1–R8 (PA2)

**Quyết định Human:** [Issue #373](https://github.com/trung-via/ai-commerce/issues/373), 2026-11-05. **Trạng thái:** HUMAN_SELECTED_ARCHITECTURE / AUTHORIZED_PLANNING_ONLY; BQ_IMPLEMENTATION_NOT_STARTED.

Đây là **phần mở rộng Work Tree có version riêng**, không phải việc đổi tên Roadmap A hoặc triển khai BQ-01..06. [Work Tree v1.1](WORK_TREE_DETAILED_V1_1.md) và [JSON v1.1](WORK_TREE_V1_1.json) vẫn giữ nguyên **63 WBS ID**, trạng thái và bằng chứng lịch sử. Các ID BQ-00..BQ-06 là namespace kế hoạch bổ sung, **không cộng vào bộ 63 ID nguyên bản**. Bản máy đọc: [WORK_TREE_B_QUALITY_001_V1_0.json](WORK_TREE_B_QUALITY_001_V1_0.json).

## Quyền sở hữu và tiêu chí nền

- **Một ChatGPT Brain** trong chat hiện tại quyết định ý nghĩa/chiến lược; **một PULSE** chịu trách nhiệm runtime khi được xây dựng và cấp quyền; EF01/EF02 chỉ làm Engineering. Không tạo sáu agent, một sổ doanh thu khác hoặc tự thay Brain.
- Q1 = giả thuyết hành vi/brief; Q2 = critic/preflight; Q3 = realism knowledge/planning; Q4 = inspection trên asset thực; Q5 = sửa tối thiểu và kiểm tra bảo toàn; Q6 = học có nguồn và đề xuất brief tiếp theo.
- K1 Visual; K2 Physical/Causal; K3 Identity/Product; K4 Temporal/Spatial; K5 Human/Audio; K6 Evidence/Trust/Rights. R1–R8 theo [bản tổng hợp](../research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md).
- Ba schema đóng CreativeDirective.v1, BrainDecision.v1, ProductJourneyReceipt.v1 không sửa. ModelCapabilityProfile và PerceptualCalibration là lớp liên hệ xuyên suốt, không phải chủ thể quyền mới. Sidecar ref/digest **mới là đề xuất**, chưa bảo đảm wire compatibility.
- Source-only/offline có thể nghiên cứu mà không cấp quyền provider. Mọi live generation, publishing, spend, rights và private token custody phải qua B0-S cùng giấy phép/receipt riêng.

## Bảy work package

| ID | Tên gói | Prio | Phụ thuộc BQ bắt buộc | Owner | Trạng thái |
|---|---|---|---|---|---|
| **BQ-00** | Kiến trúc CIMQ+CRI, registry chất lượng và ràng buộc sidecar | P0 | — | Media & Creative | IMPLEMENTATION_NOT_STARTED |
| **BQ-01** | Q1 behavioral brief + Q3 CRI: causal shots, con người, tiếng Việt và compiler mock | P0 | BQ-00 | Media & Creative | IMPLEMENTATION_NOT_STARTED |
| **BQ-02** | Q2 adversarial critic + Q4 actual-asset multimodal quality inspection | P0 | BQ-00, BQ-01 | Media & Creative | IMPLEMENTATION_NOT_STARTED |
| **BQ-03** | R6 hiệu chuẩn cảm nhận bằng blind/pairwise Human evaluation | P1 | BQ-02 | Media & Creative | IMPLEMENTATION_NOT_STARTED |
| **BQ-04** | Q5/R7 targeted media repair, preservation và re-inspection | P1 | BQ-02 | Media & Creative | IMPLEMENTATION_NOT_STARTED |
| **BQ-05** | PULSE Studio integration theo capability, quyền riêng và một provider proof | P1 | BQ-01, BQ-02, BQ-04 | PULSE coordination + Media & Creative assets | IMPLEMENTATION_NOT_STARTED |
| **BQ-06** | Q6 source-linked behavioral learning và next-Brain-brief | P1 | BQ-00 | Commerce Opportunity + Media & Creative; A09/A11 retain truth | IMPLEMENTATION_NOT_STARTED |

## Deliverables, consumers và acceptance (định hướng — chưa nghiệm thu)

### BQ-00 — Kiến trúc CIMQ+CRI, registry chất lượng và ràng buộc sidecar

- **Cắt theo Roadmap:** B0; B0-S chỉ gate external effects. **Phụ thuộc ngoài BQ:** B0 source contracts.
- **Consumers/owner liên quan:** PULSE Bridge, A03, A06, A09, A13.
- **Deliverables:** ADR B002 và bản đồ Q1–Q6/R1–R8/K1–K6; Phương án ref/digest sidecar, không sửa schema đóng; Owner/consumer và negative AC có phiên bản.
- **AC dương (khi triển khai sau này):** Danh sách owner/consumer và dữ liệu Q/R/K khớp roadmap; schema CreativeDirective.v1, BrainDecision.v1, ProductJourneyReceipt.v1 không thay đổi.
- **AC âm:** Không cấp quyền PULSE/provider và không nhận việc thay schema v1 bằng sidecar chưa được migration.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-01 — Q1 behavioral brief + Q3 CRI: causal shots, con người, tiếng Việt và compiler mock

- **Cắt theo Roadmap:** B1.1–B1.4. **Phụ thuộc ngoài BQ:** A03 C01–C03, B1.1, B1.2, B1.3.
- **Consumers/owner liên quan:** A03, A05, A12 optional, PULSE Studio future.
- **Deliverables:** Brief và creative hypotheses có nguồn/counterexample; Shot graph nhân quả, identity và action constraints; K1–K6 capability-aware plan; thử nghiệm voice/acoustic tiếng Việt offline.
- **AC dương (khi triển khai sau này):** Brief cho product_ref=null, silent, fiction, animation; compiler output có model constraints và kiểm tra tính nhất quán kịch bản.
- **AC âm:** Không chọn model/provider tốt nhất nếu chưa có benchmark; không tự tạo product facts hoặc hợp thức hóa lời chứng thực giả.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-02 — Q2 adversarial critic + Q4 actual-asset multimodal quality inspection

- **Cắt theo Roadmap:** B1.5. **Phụ thuộc ngoài BQ:** A06, A13, B1.5.
- **Consumers/owner liên quan:** A06, A13, PULSE Studio future.
- **Deliverables:** Critic preflight độc lập trước render; Inspector đa tầng trên asset có timestamp/region/ref SHA; Finding/confidence/escalation và adverse examples.
- **AC dương (khi triển khai sau này):** Phân biệt Q2 đánh giá ý tưởng/counterevidence với Q4 đo media thật; mỗi finding gắn ảnh/video/âm thanh đúng hash.
- **AC âm:** Không báo kiểm chứng đa phương thức nếu chưa mở asset; điểm quality không cấp publish/rights.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-03 — R6 hiệu chuẩn cảm nhận bằng blind/pairwise Human evaluation

- **Cắt theo Roadmap:** B1.5 calibration. **Phụ thuộc ngoài BQ:** Human reviewer consent.
- **Consumers/owner liên quan:** Q2, Q4, BQ-04.
- **Deliverables:** Bộ mẫu blind/pairwise chuẩn hóa; Inter-rater disagreement, bias và calibration report; Ngưỡng quyết định có uncertainty.
- **AC dương (khi triển khai sau này):** Mẫu, phiên đánh giá và bất đồng giữa người chấm truy nguyên được; phân biệt synthetic khỏi live perceptions.
- **AC âm:** Không suy luận viral/ROI từ điểm thích của reviewer; không dùng dữ liệu cá nhân không được cấp.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-04 — Q5/R7 targeted media repair, preservation và re-inspection

- **Cắt theo Roadmap:** B1.5. **Phụ thuộc ngoài BQ:** A05, A06, A13, A15. **Điều kiện phụ:** BQ-03 when perceptual thresholds drive repair.
- **Consumers/owner liên quan:** A05, A06, A13, A15, PULSE Studio future.
- **Deliverables:** KEEP/local-edit/shot-regenerate/return-to-Brain/HOLD decision; Scoped repair input/output hashes, cost and rights checks; Preservation assertions và measured re-inspection.
- **AC dương (khi triển khai sau này):** Chỉ phần được phép thay đổi; before/after asset digest, identity/claim preservation và Q4 reinspection minh bạch.
- **AC âm:** Không tự sửa creative strategy hoặc retry provider khi UNKNOWN_EFFECT; generative repair cần quyền riêng.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-05 — PULSE Studio integration theo capability, quyền riêng và một provider proof

- **Cắt theo Roadmap:** B0-S then B1.2/B1.5. **Phụ thuộc ngoài BQ:** B0.2/B0.3 attended runtime, B0-S independent custody, separate Human/provider/asset-rights/spend grants.
- **Consumers/owner liên quan:** PULSE Core, PULSE Studio, A04, A06, A09.
- **Deliverables:** ModelCapabilityProfile/PerceptualCalibration refs; Attended media plan-to-asset trace with private custody; Một provider proof chỉ khi có grants hợp lệ.
- **AC dương (khi triển khai sau này):** Một path offline có media QA và WAIT_BRAIN; live path chỉ ghi PASS nếu provider receipt + per-effect auth tồn tại.
- **AC âm:** Không trộn EF02 engineering runner với PULSE scheduler; không coi GitHub issue là provider grant; không ghi GO_PUBLISH tự động.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

### BQ-06 — Q6 source-linked behavioral learning và next-Brain-brief

- **Cắt theo Roadmap:** B2 → B3. **Phụ thuộc ngoài BQ:** B2 actual source/window/asset metrics for audience outcome claims.
- **Consumers/owner liên quan:** A09, A11, A17, PULSE Bridge, B3.
- **Deliverables:** Learning record: source/cohort/window/asset/metric/uncertainty; Counterevidence và next Brain brief proposal; Attended Brain accept/revise/HOLD with versioned feedback.
- **AC dương (khi triển khai sau này):** Tách outcome observed/supplied/synthetic, không so sánh nhầm window; Brain nắm quyết định thay đổi brief.
- **AC âm:** Không tự sửa chiến lược hoặc suy đoán causal lift, doanh thu, ROI khi thiếu dữ liệu.
- **Status:** AUTHORIZED_PLANNING_ONLY / IMPLEMENTATION_NOT_STARTED. Không có test/live receipt BQ nào được tuyên bố tại bản kế hoạch này.

## Thứ tự và cổng thực thi

1. **Offline:** BQ-00 (hợp đồng/architecture) → BQ-01 (brief/CRI/compiler mock) → BQ-02 (critic + inspector) → BQ-03 khi cần hiệu chuẩn; BQ-04 chỉ sau đo và bảo toàn.
2. **Protected runtime:** BQ-05 phải có B0.2/B0.3 và **B0-S private custody**; mỗi provider call cần quyền/asset rights/chi phí riêng. Không lấy EF02 source PASS làm publish clearance.
3. **Measurement & learning:** BQ-06 chỉ kết luận hành vi từ B2 source/window/asset/metric phù hợp. Thiếu observation thì UNKNOWN/NO_DATA và gửi đề xuất cho Brain, không tự đổi creative strategy.
4. **Optional:** B4 affiliate/economics/own-brand không gate content-only. Product_ref=null, silent, fiction, animation đều hợp lệ.

## Versioned adoption proof

Đọc [Roadmap B](ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md), [ADR B002](../architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md) và [tổng hợp Q1–Q6/CRI](../research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md). Bản tài liệu này **không** đăng ký executable owner, phát hành runtime hoặc cập nhật registry MT-01. Khi thực hiện từng BQ sau này phải có WorkItem, AC âm/dương, tests, source PR và quyền riêng.
