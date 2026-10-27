# AI COMMERCE BLUEPRINT v1.2 — ARCHITECTURE COMPLETION (INTEGRATED AMENDMENTS)
**Ngày:** 27/10/2026 | **Trạng thái:** RESEARCH DRAFT / NOT CANONICAL | **Tác giả:** AI Commerce Brain — advisory
**Đọc cùng:** toàn bộ §1–27 và Phụ lục A–C của Blueprint v1.1. Bản DOCX v1.2 kết hợp tài liệu gốc đã hiệu chỉnh với nội dung §28–§38 dưới đây.
**Phạm vi thay đổi:** khóa độc lập dự án; điều chỉnh quyền, bằng chứng, mô hình trạng thái; bổ sung contracts, orchestration tối thiểu, dữ liệu học, pháp lý cập nhật, ma trận triển khai và governance. Không triển khai phần mềm, không kích hoạt account, không cấp quyền, không canonical hóa.

## 28. Quyết định kiến trúc gốc: AI Commerce độc lập
**DEC-A01 — ADOPTED AS HUMAN INTENT / NOT REPOSITORY-CANONICAL.** AI Commerce là sản phẩm và dự án độc lập hoàn toàn với Python Agent. Không chung repo, worktree, runtime, cookie/profile, token, event queue, budget, hệ thống TASK/RUN hay lifecycle mặc định. Python/FFmpeg/Playwright nếu được chọn sau này chỉ là công nghệ; tên thư viện không cấu thành quan hệ sở hữu dự án. Một component bên ngoài chỉ được đưa vào qua ExternalComponentAdoptionRecord (nguồn, ref/version, giấy phép, khả năng tương thích, dữ liệu/credential liên quan, tests, rollback, người phê chuẩn).
**DEC-A02 — PRODUCT SCOPE.** Đây là Commerce Intelligence System, không phải ứng dụng đăng video: phát hiện cơ hội → hiểu audience → giả thuyết creative → sản xuất hợp lệ → phân phối được phép → quan sát/kinh tế → học có giới hạn → quyết định tăng trưởng; affiliate-first và option own brand dựa vào evidence. Hai vòng Audience Growth và Affiliate Commerce giữ mục tiêu độc lập (§1, §3).
**DEC-A03 — BRAIN NAMING.** `AI Commerce Brain` là advisory architect/research/governance assistant; `C02 Creative Reasoner` là chức năng sáng tạo trong sản phẩm. Chúng không có cùng thẩm quyền. AIOS/Python Agent không phải kiến trúc lõi và không có đường dispatch mặc định của sản phẩm này.
**Ownership mapping:** Human Principal (mandate/authority), Commerce Opportunity (opportunity, B01), Product Intelligence (product truth/offer evidence), Media/Creative (creative, experiment hypotheses), Distribution (channel capability/placement/publication state/metric provenance), Commerce Operations (workflow, asset custody, cost, reconciled settlement). Không có một AI chung được quyền ghi mọi object.
**Acceptance:** bất kỳ tài liệu hoặc code nào khai báo import trạng thái, thông tin đăng nhập, repository hay task history của Python Agent/AIOS mà thiếu quyết định adoption riêng phải bị coi `CROSS_PROJECT_DEPENDENCY_NOT_AUTHORIZED`.

## 29. Kiến trúc 5 miền, 20 capability và 6 work packages
20 ID M01–M05, C01–C06, D01–D03, L01–L04, O01, B01 vẫn là capability portfolio (§6–7), KHÔNG đồng nghĩa 20 process/agent/microservice. `L01` thuộc Distribution cho metric provenance; `L02` thuộc Commerce Operations cho settlement; `L03` thuộc Media/Creative cho experiment semantics; `L04` và `B01` thuộc Commerce Opportunity. Governance / Legal / Source access là cross-cutting policy inputs có người quyết định, không phải một domain tự nắm toàn quyền.

**MVP vertical slice**: WP1 access/source/link → WP2 brief/concepts → WP3 assets/render → WP4 claims/rights/quality → WP5 authorized or assisted publication → WP6 real observation/economics. Cho phép thao tác thủ công ở nơi API chưa đủ bằng chứng. Công cụ tích hợp tối thiểu đề xuất là *modular application* với một worker và file manifests/state bền, typed schemas; đó là phương án đầu tiên, không phải quyết định công nghệ cố định. Testability/traceability bắt buộc ở hành động rủi ro; chọn DB/queue/multiworker sau khi có lý do vận hành rõ.

**Explicit boundaries:**
- Product Collector không tự cấp quyền sử dụng media tải về, không biến seller assertions thành claims đã kiểm chứng.
- Opportunity ranking là recommendation, không tự chọn ngân sách, kênh, stock.
- Creative model không đặt `PUBLISHED`, `SETTLED` hay `AUTHORIZATION_GRANTED`.
- QA không tự cấp giấy phép hoặc phán quyết luật; chỉ kiểm theo LegalPolicySnapshot được quản trị.
- Publisher không chọn product hoặc đổi objective của campaign.
- Workflow Operations không nắm sự thật thương mại, chỉ lưu contract/result/evidence, chi phí và reconciliation.

## 30. Contract stack và data lineage liên miền
Một entity có `id`, `schema_version`, `created_at`, `updated_at`, `origin`, `source_refs`, `owner_domain` và `evidence_status` khi thích hợp. `external_id` và identity nội bộ phải tách nhau. Không có field JSON thì là `NOT_RETURNED/NOT_GRANTED/NOT_MEASURED/REDACTED/STALE/NOT_APPLICABLE`, không tự điền zero. Giữ nguyên raw observation và metric definition khi normalize.

| Interface | Input/authority | Output owner | Invariant chính |
|---|---|---|---|
| `OpportunityHypothesis.v1` | TrendSourcePack, AudienceProfile, ProductEvidence | Commerce Opportunity | ranking ≠ quyết định, có phương án ngược |
| `ProductEvidenceProfile.v1` | listing snapshot, proof, offer expiry | Product Intelligence | seller_asserted ≠ verified |
| `ContentBrief.v1` | mandate, audience, optionally product | Media/Creative | product_ref null hợp lệ với growth; primary objective fixed trước render |
| `CreativeHypothesis.v1` | briefs + evidence | Media/Creative | rationale, counterevidence, claim map, experiment design |
| `AssetRightsRecord.v1` | source, license, rightsholder, scope | Product/media asset custody theo quyền gốc | UNKNOWN/EXPIRED cấm release cần rights |
| `ApprovedPublishPackage.v1` | render+QA, allowed account/surface, AuthorizationEnvelope | Distribution | immutable hash, scope, expiry, license/claim/disclosure checks |
| `PublicationRecord.v1` | provider attempt/receipt | Distribution | upload ≠ public; unknown persists; idempotency |
| `PerformanceObservation.v1` | official/manual export with timestamp | Distribution raw provenance | raw source/metric definition retained, quality tier |
| `CommerceOutcome.v1` | affiliate settlements, cost ledger, adjustments | Commerce Operations | settled vs estimate, double count ban, attribution tier |
| `LearningUpdate.v1` | preregistered hypothesis, outcome and confounders | Media/Creative | association ≠ causality; update can be inconclusive |
| `OwnBrandDecisionBrief.v1` | audience+economics+supplier constraints | Commerce Opportunity | advisory; no automated PO |

**Contract validation:** input schema check → source/evidence/identity check → domain transform → owner-labeled output → immutable manifest/receipt → downstream consumer read. Backward-incompatible schema needs conversion/migration test and decision record; never silently reinterpret prior data. Mỗi record mang `observed_at` và `valid_at` riêng khi có ý nghĩa. Capture a claim proof by source hash, not by a model's restatement.

## 31. Authorization, G0–G6 và hành động có side-effect
Các gate là điều kiện áp dụng theo **hành động** chứ không phải các phê duyệt tuần tự bắt mọi công việc phải chờ. G0 commercial permission chỉ áp dụng shoppable placement; Audience Growth với `product_ref=null` không cần G0 commerce, nhưng phát hành vẫn phải có rights, QA và quyền tài khoản. G1 nguồn/claims/rights, G2 chất lượng, G3 sự thật trạng thái publish, G4 measurement, G5 quyết định scale, G6 own-brand. PASS trước không tự cho PASS sau; nghiên cứu có thể tiếp tục khi phát hành bị chặn.

**AuthorizationEnvelope tối thiểu:** `envelope_id`, `principal`, `delegate_actor`, `action_type`, `target_account`, `platform`, `market`, `surface`, `product_scope?`, `content_hash?`, `max_spend`, `max_posts_per_period?`, `valid_from`, `expires_at`, `stop_conditions`, `revoked_at?`, `decision_ref`. Kiểm tra lúc tạo action và ngay trước effect; nếu policy/permission thay đổi, `REVALIDATION_REQUIRED`. Một approval cho campaign có thể bao phủ nhiều action nếu scope đủ rõ, không buộc Human duyệt mỗi shot hay mỗi render. `Human decision` không cấp thay quyền API/platform hoặc quyền IP.

**CommercialAccessProfile:** phân biệt `ELIGIBLE`, `INELIGIBLE`, `UNKNOWN`, `EXPIRED`, `REVOKED`; quyền link-share không hàm ý product anchor; programme, follower band và video cap không thể quyết định chỉ qua thông tin chung. TikTok Shop VN có Creator Adjustment Program (200–999 followers; 7 ecommerce videos và 4 LIVE/tuần trong chương trình theo trang 12/10/2026) đồng thời còn trang hướng dẫn creator thông thường nêu 1.000 followers (17/07/2026); đây là **policy paths khác nhau**, cần kiểm eligibility theo hồ sơ thật, không hòa giải bằng suy luận. [S01,S10]

**Negative scenarios:** account revoked, app unaudited, wrong market, cap reached, seller source no license, link surface unclickable, expired rights, permission expiry. Block chính xác hành động không được phép; `UNKNOWN` không được nâng thành `ALLOWED`.

## 32. Publication state machine đúng trạng thái và reconciliation-first
Không dùng một cờ `success` cho việc làm video/đăng/thấy công khai/kiếm tiền. Có hai dimensions tách biệt:

1. `pipeline_state`: `DRAFT_BRIEF | SCRIPT_CANDIDATE | SCRIPT_APPROVED | ASSET_READY | RENDERED | QA_REVIEW | READY_FOR_PUBLISH | SUBMISSION_REQUESTED | METRICS_PENDING | MEASURED | REVIEWED`.
2. `publication_state`: `NOT_ATTEMPTED | MANUAL_ACTION_REQUIRED | SUBMITTED | PRIVATE_ONLY | PUBLISHED_CONFIRMED | REJECTED | UNKNOWN | REMOVED`; và `visibility`: `PUBLIC | FRIENDS | PRIVATE | UNKNOWN`. `PUBLISHED_CONFIRMED` cần xác minh post đúng account; `PUBLIC` thêm visibility proof. `commerce_link_state`: `NOT_APPLICABLE | VERIFIED_ACTIVE | UNVERIFIED | EXPIRED | REVOKED | REMOVED`.

Tên `PUBLISHED` của §3 và §11 v1.1 được chuẩn hóa thành `PUBLISHED_CONFIRMED` ở contract tương lai, với migration mapping explícit; dữ liệu v1.1 không tự được nâng trạng thái. Một màn hình upload successful không cấp phép báo `PUBLIC`.

**Reconciliation-first algorithm:** trước effect tạo `intent_id` và `idempotency_key` theo approved hash × target account × placement × intended schedule; lưu request/attempt ID; nếu timeout, tra receipt/provider status và account content; nếu còn chưa biết, giữ `UNKNOWN_REQUIRES_RECONCILIATION` và handoff với original evidence, không retry khi nguy cơ duplicate/charge tồn tại. `PUBLISHED_CONFIRMED` và `VERIFIED_ACTIVE` không suy `ORDER_CREATED`, `COMMISSION_ESTIMATED` hoặc `SETTLED`.

**Channel reality (27/10/2026):** TikTok Content Sharing Guidelines 04/08/2026: unaudited Direct Post client private-only; internal-only utility app là intended use không acceptable; cần account/app/scopes proof. YouTube `videos.insert` API page hiện hiển thị unverified projects không restricted, nhưng revision history 28/07/2020 nói unverified post-2020 projects private-only; giữ `DOC_CONFLICT_UNRESOLVED`, thử bằng chính client/account có quyền trước khi gắn SUPPORTED. [S02,S03,S04]

## 33. Data access, metric definitions, economics và learning loop
`MeasurementPlan.v1` chứa `primary_metric`, `denominator`, `unit`, `platform_metric_definition_version`, `account`, `cohort/window/timezone`, `data_source`, `export_method`, `freshness`, `missingness_rule`, `attribution_tier`, `refund/settlement lag`, `cost_scope` và `review_at`. Audience Growth KPI attention có chất lượng, retention/relevance; Commerce KPI qualified clicks, eligible orders, commission theo giai đoạn, settled contribution. Không đối chiếu view giữa nền tảng khi định nghĩa khác, hoặc giữa trước/sau policy metric cutover, nếu chưa normalize minh bạch.

**New source freshness:** YouTube revision history 27/08/2026 mô tả cách đếm public views trên các format thay đổi; không được coi số view của các cohort/metric definitions khác là cùng đo lường khi chưa kiểm. [S08]

**Attribution:** A deterministic joined; B platform-reported; C aggregate/cohort; D directional/unknown. Per-video profit chỉ khi phương pháp tương thích tier thực tế; seller/shop reports không thay thế Creator Center export của tài khoản đúng loại. TikTok Earnings Analytics 30/03/2026 phân biệt Order Created/Settled, báo export affiliate-order ở Creator Center; chưa có bằng chứng tài khoản dự án có quyền export, schema hay join. [S05]

**CostLedger:** `provider`, `model/version`, `request_id`, `reserved_cost`, `charged_cost`, `currency`, `FX_basis`, `tokens/seconds`, `usable_output`, `manual_labor_scope`, `paid_distribution`, `refund/adjustment`, `allocation_method`, `campaign`, `cost_state`. Đơn vị cạnh tranh là **chi phí trên creative/video sử dụng được và settled contribution theo scope**, không đơn thuần cost per generated second. Với API timeout và charge uncertain, không tự chạy lại request trả phí.

**Learning:** đơn vị học `CreativeHypothesis + Context + Version + Publication + Observation + Outcome + Confounder`. Pre-registration trước campaign giúp ngăn chọn metric hậu nghiệm. `OBSERVATIONAL_SIGNAL`, `DIRECTIONAL_EXPERIMENT`, `CONTROLLED_EXPERIMENT`, `INCONCLUSIVE`, `CONTRADICTED`, `STALE`; không dùng last-click correlation làm causal proof. Pattern có context, negative examples, freshness, counterfactuals.

## 34. Legal/compliance mapping và dữ liệu cá nhân
`LegalPolicySnapshot` có jurisdiction, activity, account role, platform, category, effective_from, checked_at, source URI/version, disclosure, AI label, product claims, data processing/retention, allowed action, take-down, tax/accounting touchpoint và `applicability_status`: `REVIEWED_FOR_SCOPE | NEEDS_LEGAL_REVIEW | NOT_APPLICABLE_WITH_RATIONALE | UNKNOWN`.

**Sources checked 27/10/2026:** Luật TMĐT 122/2025/QH15 hiệu lực 01/07/2026; Luật sửa đổi Luật Quảng cáo 75/2025/QH15 hiệu lực 01/01/2026; **bổ sung** Nghị định 248/2026/NĐ-CP quy định chi tiết một số điều của Luật TMĐT, hiệu lực 01/07/2026. [S06,S07,S09] Kiểm tra văn bản cụ thể và vai trò creator/seller/operator trước khi kết luận nghĩa vụ nào áp dụng; nguồn về sự tồn tại/hiệu lực **không** chứng minh kiểm tra đầy đủ các điều khoản.

**Commercial release checklist:** applicable disclosure, product/category restrictions, registered seller assertion vs actual claim, rights for footage/audio/voice/AI visual, affiliate placement, consent/scopes, account capability, takedown contingency. Nếu asset/license/commission link thu hồi: xác định ảnh hưởng đến scheduled/published posts, hold new posts, disable link/remove theo permission; nếu không có quyền thao tác, tạo handoff cho Human và lưu receipt/unknown. Không khẳng định đã gỡ khi chưa xác minh.

## 35. Architecture decision record và contract-first engineering
Mỗi quyết định kỹ thuật trọng yếu có `ADR`: `decision_id`, `context`, `options`, `chosen`, `tradeoffs`, `evidence_ref`, `owner`, `effective_at`, `revisit_trigger`, `migration`. Ưu tiên một ứng dụng modular, không mặc định multiagent, DB, queue, cloud hay GPU. `Module Contract` gồm input/output schema, semantic owner, trust/authority boundary, side effects, error taxonomy, expected cost, acceptance/negative cases, version upgrade plan.

**Task/engineering loop riêng:** Human/AI Commerce Brain chọn scope và acceptance → executor triển khai trong repo AI Commerce được cấp quyền → targeted tests → independent verification khi rủi ro cần → merge/release theo quyền → observe/repair dựa trên evidence. Không nhập TASK numbering hoặc auto-router từ AIOS. Không tự dispatch từ bản nghiên cứu; không đòi mọi thay đổi qua một hội đồng audit cố định.

**WorkItem min:** `work_id`, `objective`, `source_decision`, `scope_in/out`, `owner`, `artifacts`, `acceptance`, `evidence_required`, `risk_class`, `side_effect_class`, `status`; failure report gồm `stage`, `error_code`, `input_manifest_digest`, `provider_attempt`, `last_state`, `logs/evidence`, `retry_safety`, `next_action`, `cost_exposure`. Không làm mất log nguyên nhân chỉ vì một tóm tắt quá ngắn. Bản thiết kế đạt đủ để code ≠ đã code ≠ tests PASS ≠ live rights PASS.

## 36. Product readiness: gates không trở thành cổng hành chính
**Readiness được xét trên claim cụ thể:**
- `DESIGN_RECONCILED`: owner, schemas, alternatives, negative paths và unknowns đã mô tả.
- `BUILD_VERIFIED_FOR_SCOPE`: code đã triển khai và targeted/negative checks tương ứng PASS.
- `OPERATIONAL_E2E_PASS`: ít nhất một vòng thực đi source→creative→render→QA→authorized publish/assisted→observation có receipts, không suy ROI.
- `COMMERCIAL_DATA_ACCESS_CONFIRMED`: actual account export và attribution tier đã xác định.
- `COMMERCE_VERDICT`: `VALIDATED_FOR_SCOPE | PROMISING_DIRECTIONAL | NEGATIVE | INCONCLUSIVE | NOT_TESTED` với economics.
- `SCALE_AUTHORIZED`: Human cho phép tăng rủi ro/budget theo evidence và downside.

Các Pilot 0–4 của §16 là **protocol ứng viên để xác minh tuyên bố sản phẩm**, không phải những phê duyệt universal buộc phải chạy trước mọi task. Baseline creative reasoner 24 briefs là đề xuất nội bộ; lựa chọn mức kiểm tra do scope và mức tuyên bố quyết định, không hardcode mẫu 24 vào Constitution. Chưa có bằng chứng account, rights, live posting, settled economics hoặc benchmark -> mọi trạng thái tương ứng là `UNVERIFIED`.

## 37. Decision register, residual unknown và kế hoạch làm rõ
### 37.1 Quyết định đã chốt trong hội thoại
- AI Commerce độc lập hoàn toàn với Python Agent. `DEC-A01` = Human intent, chưa canonical repository.
- Xây Manifesto / Constitution / Project Contract và hoàn thiện Blueprint = phạm vi work nghiên cứu hiện tại; chưa có lệnh triển khai hoặc chi tiêu.

### 37.2 Quyết định còn mở; không làm nghẽn nghiên cứu
`D-01 niche`, `D-02 primary platform`, `D-03 exact account/creator program`, `D-04 campaign risk envelope`, `D-05 assisted/manual publication`, `D-06 rights/real footage`, `D-07 actual data export`, `D-08 scale threshold`, `D-09 own brand capital`, `D-10 source access contracts`, `D-11 AI model/provider selections`. Chủ sở hữu là Human/delegate; thiếu chỉ chặn side effect phụ thuộc.

### 37.3 Bằng chứng tối thiểu cần thu theo hành động
1. Account/permission: đúng account, market, program, app, scopes, placement, checked/expires, cap/health.
2. Assets/product: title/listing identity, claim sources + freshness, footage/license per scene, AI disclosure.
3. Publish: authorized surface, intent, hashes, post ID/URL, privacy, commerce link status, retries/reconciliation.
4. Economics: real report/export and data dictionary, paid/refund/settled status, cost ledger, attribution tier, cohort.
5. Scale/own brand: multi-cycle evidence, supplier/manufacturing cost, MOQ/returns, working capital and loss scenarios.

## 38. Source record & supersession
**Đối chiếu nguồn online ngày 27/10/2026 (policy-level, không phải account proof):**
- S01 TikTok Shop Creator Adjustment Program (12/10/2026): https://seller-vn.tiktok.com/university/essay?knowledge_id=2356948167771905
- S02 TikTok Content Sharing Guidelines (04/08/2026): https://developers.tiktok.com/docs/en/content-sharing-guidelines
- S03 YouTube Data API videos.insert, trạng thái trang được truy xuất 27/10/2026: https://developers.google.com/youtube/data/reference/rest/v3/videos/insert
- S04 YouTube Data API revision history 28/07/2020 (và 27/08/2026 measurement update): https://developers.google.com/youtube/data/revision_history
- S05 TikTok Shop VN Earnings Analytics (30/03/2026): https://seller-vn.tiktok.com/university/essay?knowledge_id=2782803196610305&lang=en
- S06 Luật TMĐT số 122/2025/QH15 (10/12/2025; hiệu lực 01/07/2026): https://vanban.chinhphu.vn/?classid=1&docid=216503&pageid=27160&typegroupid=3
- S07 Luật sửa đổi Luật Quảng cáo số 75/2025/QH15 (16/06/2025; hiệu lực 01/01/2026): https://vanban.chinhphu.vn/?classid=1&docid=214561&pageid=27160
- S08 YouTube revision history: public views update (27/08/2026): https://developers.google.com/youtube/data/revision_history
- S09 Nghị định 248/2026/NĐ-CP (30/06/2026; hiệu lực 01/07/2026): https://vanban.chinhphu.vn/?docid=218747&orggroupid=2&pageid=27160
- S10 TikTok Shop VN How to Become a Creator (17/07/2026): https://seller-vn.tiktok.com/university/essay?knowledge_id=6837793229817601&lang=vi-VN

**Supersession rule:** §28–§38 v1.2 điều chỉnh những câu chữ mâu thuẫn trực tiếp của v1.1, còn lại giữ nguyên các định nghĩa và 25 AC v1.1. `PUBLISHED` trong v1.1 chuyển thành `PUBLISHED_CONFIRMED` trong schema đề xuất mới; các record/implementations cũ chưa được migrate. Tất cả đều là DRAFT, không có quyền triển khai hay lời xác nhận các nguồn dữ liệu/account đã sẵn sàng.
