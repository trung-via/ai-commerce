# B-QUALITY-001 v1.0 — Creative Intelligence, Realism & Media Quality (CIMQ + CRI)

**Hướng kiến trúc:** PA2/PULSE được Human chọn ngày 2026-11-05 theo [Issue #373](https://github.com/trung-via/ai-commerce/issues/373). **Phạm vi tài liệu này:** tổng hợp kế hoạch Q1–Q6, CRI R1–R8 và K1–K6; không công bố model benchmark, đa phương tiện thực được sản xuất, provider rights, source code BQ chạy hay kết quả kinh doanh. Các vấn đề gốc Q1 [#337](https://github.com/trung-via/ai-commerce/issues/337), Q2 [#342](https://github.com/trung-via/ai-commerce/issues/342), Q3 [#344](https://github.com/trung-via/ai-commerce/issues/344), Q4 [#346](https://github.com/trung-via/ai-commerce/issues/346), Q5 [#347](https://github.com/trung-via/ai-commerce/issues/347), Q6 [#351](https://github.com/trung-via/ai-commerce/issues/351) là nghiên cứu, không phải receipt runtime.

## 1. Một hệ thống, thẩm quyền tách biệt

- **ChatGPT Brain duy nhất (chat hiện tại):** đặt mục tiêu sáng tạo, Human-selected brief, ý nghĩa bằng chứng, chọn/đổi chiến lược và đánh giá kết quả.
- **PULSE runtime duy nhất:** điều phối tác vụ media được Human cấp quyền sau khi có boundary độc lập; Core/Ledger/Studio/Bridge/Adapters là các module logic, không phải các agent tự chủ có thẩm quyền riêng. Ledger PULSE chỉ chứa trạng thái phối hợp/ref/SHA, không thay năm semantic owner ledger đã có.
- **Engineering EF01/EF02:** xây, sửa, kiểm thử source, không tự chạy product journey thường lệ hay giữ credential/provider token.
- **Human:** cấp quyết định kiến trúc và quyền external-action riêng, không ủy quyền publish/spend thông qua một GitHub issue hoặc một quality score.

## 2. Sáu chức năng Q, không phải sáu agent

| Năng lực | Đầu vào, chức năng | Đầu ra có thể kiểm chứng | Giới hạn trách nhiệm |
|---|---|---|---|
| **Q1 — Behavioral Creative Intelligence (BCI)** | Audience, ý định và giả thuyết hành vi, tiêu chí kể chuyện | Brief/creative hypothesis, đối chứng và nguồn dự kiến | Giả thuyết ≠ sự thật hành vi; Q1 không tự ghi kết quả analytics |
| **Q2 — Adversarial Creative Critic (ACC)** | Brief, kịch bản, shot plan; sau render có thể nhận finding Q4 | Counterexample, kịch bản phản biện, risk/uncertainty | Critic không tự tuyên bố đã nhìn asset thật hoặc cấp quyền phát hành |
| **Q3 — Creative Realism Intelligence (CRI)** | Intent, knowledge R/K, capability, continuity, Việt ngữ và causal constraints | Editable shot/action/acoustic plan, model-neutral capability brief | Planning không phải model inference/QA đo được; Q3 ≠ Q5 repair |
| **Q4 — Multimodal Media Quality Inspector (MMQI)** | **Asset media thực** với identity/hash/frame/time/audio | Finding có evidence ref, timestamp/region, severity và uncertainty | Không đo asset = NOT_INSPECTED; Q4 ≠ Q2 ý tưởng |
| **Q5 — Targeted Media Repair (TMR)** | Q4 findings + Brain/Human constraints + asset/rights | KEEP/edit-local/shot-regenerate/return-to-Brain/HOLD + preservation/reinspection | Không tự rewrite thông điệp hoặc retry provider khi effect còn UNKNOWN |
| **Q6 — Behavioral Quality Learning (BQL)** | Asset ref, A09/A11 source/window/metric, exposure/uncertainty, counterevidence | Versioned learning record và **đề xuất** next Brain brief | Correlation ≠ causality, dữ liệu thiếu ≠ zero; Q6 ≠ Q1 quyết định chiến lược |

Quy trình dự kiến: Brain định hướng → Q1 brief → Q3 causal production plan → Q2 preflight → PULSE Studio (chỉ khi được cấp phép) tạo/tổng hợp media → Q4 inspection → Q5 repair có giới hạn → Brain review + Human attended release → A09/A11 measurement có nguồn → Q6 learning proposal cho Brain. Bất kỳ khâu chưa có dữ liệu/rights/provenance đều HOLD hoặc UNKNOWN, không tô xanh.

## 3. Creative Realism Intelligence R1–R8

| R | Nội dung dự kiến | Điều cần chứng minh về sau |
|---|---|---|
| **R1** | Video realism: ánh sáng, texture, vật lý, chuyển động, bố cục | So sánh asset thực và failure taxonomy; không dự đoán bằng prompt text |
| **R2** | Cấu trúc knowledge và tiêu chuẩn realism có phiên bản | Mapping K taxonomy và nguồn, không trộn đánh giá với quy tắc |
| **R3** | Human performance và nhân quả hành động | Action graph, động cơ cử chỉ, contact, object continuity |
| **R4** | Quan hệ speech ↔ face ↔ gesture ↔ environment | Lip/pose/gaze/interaction sync có time evidence |
| **R5** | Ngữ điệu/âm sắc/phát âm tiếng Việt tự nhiên và âm học bối cảnh | Accent/tempo/emotion, acoustic geometry, room/reverb/audio rights |
| **R6** | Blind/pairwise human perceptual calibration | Bias, inter-rater agreement, uncertainty, không lẫn preference với revenue |
| **R7** | Critic-to-repair policy và lựa chọn chi phí/bảo toàn | Chứng cứ KEEP vs sửa, before/after hashes và asset quality không regress |
| **R8** | Kiến trúc tích hợp Brain/PULSE/Engineering và quyền | Một nguồn Brain decision, một runtime, trusted refs; không sinh authority mới |

### K1–K6 — taxonomy ổn định

- **K1 Cinematic & Visual:** shot size, camera grammar, lighting, composition, texture.
- **K2 Physical & Causal:** động lực học, tiếp xúc, va chạm, chuỗi hành động nhân quả.
- **K3 Identity & Product:** người/đối tượng/product consistency; truth/fact/rights là nguồn riêng, không model-invented.
- **K4 Temporal & Spatial:** continuity giữa shots, timebase, relative positions, transition.
- **K5 Human Performance & Audio:** pose/gesture/emotion, lip-sync, Vietnamese voice/acoustics; âm thanh im lặng cũng hợp lệ.
- **K6 Evidence / Trust / Rights:** nguồn, hash, Human/provider rights/permissions, uncertainty, revocation và auditability.

**Cross-cutting artifacts:** ModelCapabilityProfile (provider/model capability + known limits, version/provenance) và PerceptualCalibration (blind judgments + disagreement). Chúng không tạo thêm executive owner hay chứng nhận model. Có thể xây model-neutral compiler mock trước khi có thật provider access.

## 4. Media không bắt buộc sản phẩm

`product_ref=null` là hợp lệ. Content-only, fiction, meme, animation, silent video, nội dung không có giọng nói hoặc không có SKU vẫn thuộc creative path nếu disclosure/rights hợp pháp. Không bắt buộc đưa affiliate link, Product Intelligence claims hay fake testimonial vào mọi brief. Khi có sản phẩm thật, facts/claims/identity/rights lấy từ A12/P02/P07 với freshness; không tự suy từ ảnh hoặc prompt. Fictional claims không bị tái diễn giải thành factual product claims.

## 5. Evidence, security và phiên bản contract

- **Source level:** Research/Human-selected plan chỉ là planning; tests fixture ≠ live generated media. Giá trị metadata có version, sha/ref, provenance tier (synthetic, Human-supplied, provider-observed), confidence/missing/revoked và timestamp/source window.
- **Media level:** Q4/Q5 cần input/output asset SHA, region/time/audio layer, actual inspector version, findings, negative tests và preservation. Không thể tuyên bố kiểm chứng nếu chỉ có synthetic stills/video hoặc prompt.
- **Behavior level:** A09/A11 giữ quan sát gốc, cohort/window/metric, source status; Q6 chỉ đề xuất hypothesis/next brief và ghi counterevidence.
- **Trust level:** B0-S phải xác nhận private custody cùng quyền riêng trước external spend/generation/publication; cùng Windows SID, GitHub comment hay khóa schema không chứng minh custody. UNKNOWN_EFFECT giữ original idempotency key, không tự retry.
- **Wire level:** `CreativeDirective.v1`, `BrainDecision.v1`, `ProductJourneyReceipt.v1` là **closed schemas**. Dự kiến dùng sidecar refs/digests hoặc versioned future schema/migration sau review; không tự thêm trường, không nhét payload vào extras hoặc nói rằng sidecar đã được validator công nhận. Chính xác version, source digest, approved scope và final asset SHA phải được verify bởi trusted receiver; vấn đề tương thích hiện **OPEN**.

## 6. Sequencing PA2, thực thi và chấp nhận

B0 offline contracts + B0-S external trust → BQ-00 → BQ-01 (Q1/Q3) → BQ-02 (Q2/Q4) → tùy chọn BQ-03 calibration + BQ-04 repair → BQ-05 only after independent runtime grants. B2 measurement cung cấp nguồn cho BQ-06/B3 learn-back. B4 affiliate, economics, own-brand vẫn tùy chọn và không gate creative content-only.

Deliverable liên kết: [ADR B002](../architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md), [Work Tree B-QUALITY-001](../roadmap/WORK_TREE_B_QUALITY_001_V1_0.md), [Roadmap B](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md). Đối với các gói BQ-01..06, trạng thái hiện tại **IMPLEMENTATION_NOT_STARTED**; không có quyền production hay outcome nào được phát sinh từ adoption docs.
