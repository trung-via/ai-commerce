# ADR B002 — Creative Intelligence, Realism & Media Quality trong PA2

**Ngày quyết định:** 2026-11-05. **Decider:** Human @AI Commerce theo [Issue #373](https://github.com/trung-via/ai-commerce/issues/373). **Status:** HUMAN_SELECTED_ARCHITECTURE_AND_PLANNING; BQ_IMPLEMENTATION_NOT_STARTED. ADR này ghi nhận lựa chọn định hướng, **không** thay thế Brain review, PR merge receipt hoặc external authorization.

## Bối cảnh và câu hỏi

Roadmap A đã có owner riêng cho Creative/Media, Product Truth, Distribution, Analytics và Commerce Operations; PA2 tạo PULSE runtime độc lập do ChatGPT Brain dẫn dắt. Cần bổ sung creative intelligence sâu, realism/action/human Vietnamese audio, QA/repair và learning mà không biến một tập hợp Q1–Q6 thành sáu agent tự chủ, một API ChatGPT Brain ảo hay thêm sổ kinh doanh.

## Quyết định

Chấp nhận **B-QUALITY-001 (CIMQ + Q3 Creative Realism Intelligence R1–R8)** là **phần mở rộng roadmap/worktree versioned của PA2**. Giữ đúng 1 Brain chat hiện tại (strategy/meaning), 1 independent PULSE runtime (core, ledger, studio, bridge, adapters với quyền đã kiểm chứng) và EF01/EF02 chỉ cho Engineering. Năm semantic owners của A-series vẫn là nguồn truth; PULSE trỏ ref/SHA, không sao chép thành ledger thứ hai.

### Ownership chính xác

| Owner / subsystem | Trách nhiệm được đề xuất | Không được suy ra |
|---|---|---|
| Brain trong chat hiện tại | Creative strategy, brief approval, đổi concept, next-brief decision | Không tự có broker signer/provider keys, GO_PUBLISH hoặc GO_SPEND |
| Q1 BCI | Audience hypotheses và brief dạng đề xuất | Không là analytic truth hoặc quyết định chiến lược độc lập |
| Q2 ACC | Critique preflight và adversarial counterexamples | Không thế chỗ asset inspection hay nguồn quyền |
| Q3 CRI | R1–R8 realism knowledge/causal shot plan, K1–K6, capability-aware direction | Không là provider runtime hay measured output QA |
| Q4 MMQI | Inspector trên media asset thực: visual/audio/time evidence | Không bịa timestamp/region hoặc đo prompt |
| Q5 TMR | Quyết định scoped repair, preservation, reinspection | Không thay creative intent hoặc retry unknown paid generation |
| Q6 BQL | Evidence/counterevidence → đề xuất next Brain brief | Không tự đổi chiến lược, không tự nhân quả hóa ROI |
| PULSE Core/Studio/Bridge/Ledger/Adapters | Orchestrate permitted tasks + refs với separate trust/receipt | Không thay A-series Truth, không thành coding agent scheduler |
| EF01/EF02 | Build, test, repair source | Không thay attended PULSE runtime hoặc nắm quyền private provider |

ModelCapabilityProfile và PerceptualCalibration là **cross-cutting specs**, không phải chủ thể quyền mới. Các nhóm K1–K6 phải liên kết R1–R8 và tác vụ Q; chúng không được biến thành các agent riêng hoặc model-specific proprietary truth.

## Phương án và trade-off

| Phương án | Quyết định | Lý do |
|---|---|---|
| Giữ PA2 + extension B-QUALITY-001 | **Chọn** | Reuse A-series và PULSE, chia semantic owner và effect boundary rõ, tránh bản sao runtime |
| Chuyển sang Roadmap C / hệ thống sản phẩm thứ hai | Không chọn | Tăng hệ thống và authority trùng khi chưa có independent need/evidence |
| Six autonomous Q-agents cùng tự quyết | Không chọn | Nhiều quyền và cost, khó xác minh learning/strategy consistency |
| Nhét Q1–Q6 trực tiếp vào EF02 scheduler | Không chọn | EF02 là build/repair runner, không phải media product runtime |
| Chỉ chấm một creative score rồi tự publish | Bác bỏ | Quality, Human trust, provider entitlement, rights và spend là các gate khác nhau |

## Hợp đồng và migration phải giữ

Ba schema hiện tại là **closed**: `CreativeDirective.v1`, `BrainDecision.v1`, `ProductJourneyReceipt.v1`. BQ-00 chỉ đề xuất họ dữ liệu mới như CreativeQualityPlan, InspectorFinding, RepairPlan, ModelCapabilityProfile và PerceptualCalibration dưới dạng **tham chiếu versioned/ref + SHA/digest**. Không thay schema v1, không thêm field, không giả vờ `additionalProperties` cho phép sidecar bên ngoài, không khẳng định receiver đã xác minh.

Hai hướng sau mới là thiết kế cần review: (A) sidecar ngoài wire có ref/digest được receiver kiểm chứng, không nâng scope/authority; (B) schema v2 có compatibility/migration test đầy đủ. **Chưa chọn A/B để triển khai**; trạng thái BQ-00 hôm nay là planning only. Thay final asset bytes hay source approval/version phải yêu cầu bind/review lại. Human Brain acceptance và independent verified private receiver vẫn là hai cổng khác nhau.

## Q3 CRI R/K và kết quả dự kiến

R1 realism shot, R2 knowledge specification, R3 causality/performance, R4 speech-face-gesture-environment sync, R5 Vietnamese acoustic naturalness, R6 blind human calibration, R7 repair selection, R8 integration/authority. K1 Cinematic & Visual; K2 Physical & Causal; K3 Identity & Product; K4 Temporal & Spatial; K5 Human Performance & Audio; K6 Evidence/Trust/Rights. [Chi tiết Q1–Q6/R1–R8/K1–K6](../research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md).

**Content-only vẫn hợp lệ:** `product_ref=null`, fiction, meme, animation, silent/no voice. Khi có sản phẩm hoặc testimonial, không model-generate factual evidence/rights; re-open A12. Asset-level quality report không đủ cấp quyền đưa ra ngoài, không tiên đoán doanh số.

## Triển khai phân kỳ và kiểm chứng độc lập

- **B0/B0-S:** contract/version/coordination offline và private trust trước external effects. Source-only research/test được phép riêng, không mang theo permission live.
- **B1.1–B1.3:** Q1/Q3, capability-aware compiler mock, Vietnamese speech/acoustics tests. B1.4 product facts/rights tùy trường hợp.
- **B1.5:** Q2 preflight + Q4 actual-asset QA + Q5 scoped repair/preservation; R6 calibration nếu Human judge threshold cần thiết.
- **B2 → B3:** A09/A11 source-linked observations → Q6 next Brain brief proposal. B4 vẫn optional.
- **BQ-05:** connector PULSE Studio live chỉ sau B0-S independent custody, separate provider/asset/spend grants và real receipt; unsupported/UNKNOWN giữ HOLD.

**Cổng nghiệm thu BQ tương lai:** explicit WorkItem + allowed paths, updated source version + positive/negative AC, Windows registered tests, candidate PR/independent CI/Brain PASS, only then optional independent runtime proof. Source CI xanh không chứng minh model generation, media quality, audience response hay revenue. Không thử provider network, không cấp quyền và không chạy PULSE vì tài liệu này.

## Hậu quả và liên kết

Ưu điểm: kết nối Q1–Q6 và R1–R8 vào PA2 mà giữ source truth, tách kỹ thuật khỏi creative meaning, không đẩy thêm model calls cho mọi media journey. Đánh đổi: phải đo actual assets/permissions, versioned interface và independent receipt trước live; các capability plan chưa phải số liệu performance.

[Roadmap B](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md) · [Work Tree B-QUALITY-001 MD](../roadmap/WORK_TREE_B_QUALITY_001_V1_0.md) · [JSON](../roadmap/WORK_TREE_B_QUALITY_001_V1_0.json). 63 WBS của Work Tree v1.1 không thay đổi; BQ-00..BQ-06 là planning extension riêng.
