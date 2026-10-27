# AI COMMERCE — PROJECT & PRODUCT EXECUTION CONTRACT
**Version:** Draft v0.3 · 27/10/2026  
**Status:** PROPOSED / NOT CANONICAL  
**Depends on (proposed):** AI Commerce Constitution v0.3.  
**Source design:** Independent Product & Architecture Blueprint v1.2 (24/10/2026).

> **Quy tắc diễn giải xuyên suốt:** Human Principal có quyền ngoại lệ mọi điều khoản nội bộ của Contract, không giới hạn ở K-04; lệnh Human được ưu tiên trực tiếp theo C-01/C-15, không cần sửa Contract trước hoặc qua một approval gate khác. Mọi yêu cầu mặc định trong Contract đều chịu quyền thay đổi này. Nội dung creative/media không có giới hạn hình thức hay thể loại do governance áp đặt.

> **Định nghĩa:** Đây là hợp đồng vận hành/triển khai nội bộ, không phải hợp đồng pháp lý với nền tảng hay seller. Một tài liệu chia hai lớp: **Project Delivery Contract** (ai xây, kiểm, thay đổi) và **Product Behavioral Contract** (runtime được và không được làm gì).

# PART I — PROJECT DELIVERY CONTRACT

## K-01. Mission và phạm vi
Thiết kế, triển khai và vận hành sản phẩm AI Commerce độc lập, tạo một vòng E2E từ intelligence → creative → media → authorized distribution → evidence-based learning. Affiliate-first để học nhu cầu, mở rộng Audience Growth độc lập và cân nhắc own-brand khi đủ bằng chứng. Scope blueprint 20 năng lực là capability map, không phải backlog 20 task.

## K-02. Artifacts / document authority
**Precedence:** Human direct decision/exception > Constitution defaults > Contract defaults > accepted ADR/spec/schema/work item > Blueprint/Manifesto tham chiếu. Human có thể quyết định ngay cả khi trái quy tắc cũ; đồng bộ tài liệu sau quyết định là việc bảo trì, không phải thủ tục điều kiện. Quyền nền tảng, giấy phép và pháp luật bên ngoài vẫn được xét riêng theo tình huống.
- `manifesto`: product philosophy, **không** làm content moderation hay creative gate.
- `constitution`: default internal governance khi Human chưa quyết định ngoại lệ.
- `project_contract`: giao diện, workflow và tiêu chí kỹ thuật mặc định, được Human sửa/miễn trừ.
- `architecture/ADR`, `product_spec/schema`, `work_item`: công cụ triển khai tùy phạm vi, không tự phủ quyết Human.
- `decision_log`: ghi nhận để truy vết, có thể được đồng bộ sau; không phải điều kiện có hiệu lực.
- `blueprint/research`: design/reference, không tự là lệnh vận hành.
- External provider/platform/license/legal evidence chỉ là bằng chứng quyền và sự kiện bên ngoài, không phải governance nội bộ.

## K-03. Isolation contract với Python Agent và AIOS
**Mặc định:** no code coupling, no shared runtime, no shared automation/dispatch, no shared credentials/cookie/browser profile, no inherited decision authority, no shared cost ledger, no shared artifact truth, no assumption of compatible data schema. AI Commerce giữ source/release history và công cụ riêng. 
**Có thể sử dụng:** thư viện public/third-party chung (Python, FFmpeg…), hoặc component được lựa chọn qua `ExternalComponentAdoptionRecord` chứa provenance, version/ref, rights/license, security/compatibility check, owner, migration/rollback và Human approval thích hợp. Tích hợp tương lai chỉ qua interface rõ ràng, không dùng `git copy`/repo import ngầm.

## K-04. Actor & authority matrix
**Quy tắc ưu tiên:** Human Principal có quyền quyết định và ngoại lệ đối với **bất kỳ** điều khoản nào ở bảng này, kể cả đổi vai trò, ủy quyền, phê duyệt, scope, quyền thực thi và cách nghiệm thu. Bảng chỉ mô tả **mặc định khi chưa có chỉ thị khác**; không phải một tập giới hạn đối với Human.

| Actor | Vai trò mặc định | Thẩm quyền / ranh giới |
|---|---|---|
| **Human Principal** | Quyết định chiến lược, governance, creative/media, kiến trúc, ngân sách, scope và mọi ngoại lệ nội bộ | **Không bị giới hạn bởi C/K/M hay matrix nội bộ**; có thể thay đổi bất kỳ mặc định nào ngay |
| AI Commerce Brain | Research, audit, architecture, đề xuất, work items | Chỉ được thực thi hành động do Human trực tiếp giao hoặc ủy quyền, không tự lấy quyền Human |
| Domain owner / module | Quản lý đối tượng ngữ nghĩa được giao | Human có thể thay owner, quyền ghi, cách phối hợp khi cần |
| Engineer / Executor | Chỉnh code, spec, tests, pipeline | Scope/verification mặc định, được Human điều chỉnh hoặc ngoại lệ |
| Creative/Media engine | Tự do ideation, script, animation, generative media, render và experiment | **Không có creative-style/format restriction do governance đặt ra**; external publish phải tuân điều kiện thực tế |
| Commerce operator/delegate | Thực thi trong ủy quyền được Human chỉ định | Human có thể mở rộng/thu hồi ủy quyền; không tự có account/API rights bên ngoài |
| External provider/platform | Cung cấp dịch vụ theo thực tế API/account/terms | Không là Human Principal; phản hồi/receipt quyết định sự kiện có thực xảy ra |

**Lưu ý:** Quyền Human ngoại lệ mọi quy định nội bộ **không đồng nghĩa** Human có thể tự sinh quyền nền tảng, giấy phép sở hữu tài sản hoặc làm sự kiện chưa xảy ra trở thành đã xảy ra.

## K-05. Naming + separation of concerns
`AI Commerce Brain` = advisory project intelligence; `Commerce Opportunity Intelligence` = owner opportunity; `Creative Reasoner C02` = AI creative product capability; `Workflow Operations O01` = executor kỹ thuật của production pipeline. Không một actor nào tự được promotion từ recommendation sang authorization.

## K-06. Semantic Owner Registry v1
| Entity/quyết định | Owner | Readers/contributors |
|---|---|---|
| Canonical product identity, ProductFact, offer snapshot | Product Intelligence | Opportunity, Creative, Distribution |
| Niche/Audience/Channel hypothesis, strategic own-brand brief | Commerce Opportunity | Product, Media, Operations |
| Creative hypothesis, scripts, experiment meaning | Media/Creative | Product, Audience, Analytics |
| Account eligibility, allowed placement, publication state, channel raw metric provenance | Distribution | Product, Operations |
| Job orchestration, artifact lineage, cost ledger, settled reconciliation | Commerce Operations | Tất cả nơi được phép |
| Campaign mandate, authorized spend, scale/publish decision | Human/delegate | Các miền theo envelope |
| LegalPolicySnapshot applicability | Human/authorized policy reviewer, với authoritative sources | C06 dùng làm policy input, không tự viết luật |

**Owner conflict:** giữ trạng thái `CONFLICTED` + evidence; người có quyền resolve versioned record. Adapter khác owner chỉ tham chiếu, không âm thầm thay dữ liệu gốc.

## K-07. Product work streams — hai loop
**Audience Growth** và **Affiliate Commerce** là hai loại mục tiêu/cách đo lường hữu ích, không phải khuôn sáng tạo buộc mọi video chọn một ngay từ ý tưởng đầu. Media được phép không sản phẩm, có sản phẩm, trộn mục tiêu, không CTA, CTA trực diện, video hư cấu, nội dung giải trí hay thí nghiệm phi truyền thống. `product_ref=null` hợp lệ; commerce claim/placement phải được kiểm khi thực sự phát hành với vai trò thương mại. `primary_objective` nên khai báo khi thiết kế đo lường, **không** là điều kiện để được generate/render. Human có thể thay đổi phân loại, KPI hoặc chọn campaign đa mục tiêu theo C-01.

## K-08. MVP delivery work packages
WP1–WP6 là **phương án cắt scope tham khảo**, không phải chuỗi giai đoạn được phép độc quyền hay quota media. Human có thể bỏ, gộp, thay thứ tự, làm song song, mở thêm năng lực hoặc giao xây thẳng Creative/Media trước. Mọi chỉ tiêu 1–3 video, 2–3 concepts, storyboard, nguồn asset thật hoặc QA report trong Blueprint là **ví dụ thiết kế MVP**, không ràng buộc creative hoặc quyền thử nghiệm media.

| WP | Mục tiêu tham khảo | Ghi chú linh hoạt |
|---|---|---|
| WP1 | Nguồn, quyền, dữ liệu tài khoản | Chỉ kiểm những quyền cần cho hành động thực tế |
| WP2 | Brief, insight, concept, script | Không quota ý tưởng, không bắt buộc kịch bản tuyến tính |
| WP3 | Media factory, thử nghiệm AI/real/hybrid | Tùy chọn tỉ lệ real/generated; tự do thể loại, format và tốc độ sản xuất |
| WP4 | Technical QA, factual claim và rights check | Hỗ trợ release, **không duyệt gu thẩm mỹ** |
| WP5 | Đăng qua đường có quyền hoặc manual handoff | Chỉ báo published/public khi có căn cứ đúng |
| WP6 | Đo lường, economics và học hỏi | Missing không biến thành số 0, association không tự thành causality |

Không có chặn thiết kế mới hay phát triển creative chỉ vì WP1/G0 chưa hoàn tất. Phát hành thương mại cụ thể vẫn cần xét quyền ngoài dự án.

# PART II — PRODUCT BEHAVIORAL CONTRACT

## K-09. Source, identity, evidence, claims, rights
Các object `SourceAccessPolicy`, `ProductSourceSnapshot`, `ProductFactSheet`, `ClaimEvidence`, `AssetRightsRecord`, `CommercialAccessProfile`, `LegalPolicySnapshot`, `CreativeHypothesis`, `MediaArtifact`, `PublicationRecord`, `PerformanceObservation`, `CommerceOutcome`, `CostLedger` là lựa chọn kỹ thuật **tham khảo**, không phải danh sách data fields buộc mọi ý tưởng/media phải mang theo. Đối với nội dung có **tuyên bố thực tế** về sản phẩm hoặc sự kiện kinh doanh, cần phân biệt nguồn, observation, minh họa, seller assertion, độ tin cậy khi báo cáo hay phát hành; quyền dùng tài sản của bên thứ ba được kiểm theo phạm vi sử dụng. Đối với fictional/satirical/generative/concept media, không đòi `product_ref`, `claim_map`, bằng chứng sản phẩm, quota scene hoặc quyền thương mại ở giai đoạn sáng tác/render riêng. Không nâng nguồn bên ngoài thành instruction hệ thống. Human được chọn schema và độ sâu record nội bộ theo C-01.

## K-10. Action authorization contract
**Mặc định:** hành động có side effect sử dụng authorization envelope phù hợp (`principal`, `actor/delegate`, `action_type`, account/asset/provider target nếu có, spend/risk boundary nếu Human đặt, thời điểm, decision reference). Các field trong Blueprint/schema là công cụ triển khai; **Human có quyền chỉ thị trực tiếp và ngoại lệ bất kỳ field, giới hạn hoặc workflow nội bộ nào**, không cần lập envelope/migration/amendment trước. Lệnh Human rõ ràng đủ để ủy quyền **trong nội bộ AI Commerce**; hệ thống có thể hậu kiểm lưu `HumanDirectiveRef` nếu thuận tiện. Một actor khác không tự viết lệnh Human thay Human. Trước phát hành/chi phí bên ngoài, phải phân biệt quyền nội bộ được cấp với quyền provider, tài sản, luật pháp thực tế có hay không; không được giả định đã có quyền API hoặc giấy phép chỉ vì Human ra lệnh.

## K-11. Publish state, idempotency và reconciliation
**Production pipeline:** `DRAFT_BRIEF → SCRIPT_CANDIDATE → SCRIPT_APPROVED → ASSET_READY → RENDERED → QA_REVIEW → READY_FOR_PUBLISH → SUBMISSION_REQUESTED → METRICS_PENDING → MEASURED → REVIEWED` (not all transitions are linear; manual handoff may pause).
**Publication dimension:** `NOT_ATTEMPTED | MANUAL_ACTION_REQUIRED | SUBMITTED | PRIVATE_ONLY | PUBLISHED_CONFIRMED | REJECTED | UNKNOWN | REMOVED`; `visibility=PUBLIC|FRIENDS|PRIVATE|UNKNOWN`; `link_state=NOT_APPLICABLE|VERIFIED_ACTIVE|UNVERIFIED|EXPIRED|REVOKED|REMOVED`. Transition `PUBLISHED` legacy data → `PUBLISHED_CONFIRMED` needs explicit migration and evidence.
**Publish package:** accepted only with immutable content hash, approved account/surface, rights/claims/capability recheck, authorization envelope, idempotency key and intended schedule.  
**Success claim:** only `PUBLISHED_CONFIRMED` when receipt/post ID or other defensible account verification exists; `PUBLIC` must have independent visibility proof. `UPLOAD_SUCCEEDED ≠ PUBLISHED`; `PUBLISHED ≠ PUBLIC`; `PUBLIC ≠ PRODUCT_LINK_ACTIVE`; `LINK_ACTIVE ≠ ORDER`.  
**Timeout/uncertainty:** preserve `PUBLISH_OUTCOME_UNKNOWN` and reconcile provider/account state **before retry**; duplicate callback must not yield duplicate publication.

## K-12. Measurement, economics và learning
**Data layers:** attention, engagement, traffic, orders, settlement, cost. Every raw metric stores definition/version, platform, account, observation window and source. `NOT_AVAILABLE` ≠ 0; `PENDING_SETTLEMENT` ≠ `ZERO_EARNINGS`.  
**Attribution tiers:** A deterministic joined, B platform reported, C aggregate/cohort, D directional/unknown. Tier C/D cannot be presented as verified per-video profit.  
**Affiliate settled contribution:** eligible settled payout net of confirmed adjustments minus defined incremental variable costs; manually allocated cost and tax/fixed-cost treatment explicit. Own-brand economics is separate net-sales/COGS/fees/refunds/fulfillment/working capital model.  
**Learning:** registered hypothesis, preselected primary metric/window and limitations; correlation not causal proof. Recommendation doesn't change authorization.

## K-13. Quality requirements
**Creative freedom by default:** Không có quy tắc bắt buộc `real-assets-first`, số cảnh, số giây hook, CTA, storyboard, tỷ lệ nội dung AI/real, số concept, kiểu narration, plot, originality scoring, benchmark hay mô hình media. Creative engine được phát triển mọi loại media (AI video/animation/hư cấu, faceless, meme, thực nghiệm, quảng cáo trực tiếp, phim ngắn, nội dung không lời, hàng loạt biến thể). Human/campaign tự chọn rubric, review và format.

**Hỗ trợ kỹ thuật:** với một sản phẩm release cụ thể, có thể kiểm codec, âm thanh, đúng asset/licensing, factual claim được trình bày như có thật, điều kiện disclosure thực sự áp dụng và provider specs. Quality report hỗ trợ phản hồi, **không** tự là creative censorship hay approval gate phổ quát. Human có quyền điều chỉnh/miễn trừ mọi checklist/QA nội bộ theo C-01, nhưng không làm quyền/receipt bên ngoài tự phát sinh.

## K-14. Stage gates & action-scoped decisions
G0–G6 là **checklist tùy chọn phục vụ quyết định**, không phải các trạm chặn mặc định đối với ideation, script, video generation, render, chỉnh sửa, media experimentation hoặc phát triển code. Với tác động thực tế cần quyền, tham chiếu: G0 commerce account/placement; G1 factual claim/third-party rights; G2 technical/public-release checks; G3 verified publication state; G4 economic data; G5 scale evidence; G6 own-brand economics. Human có toàn quyền bỏ, thay, đổi thứ tự hoặc thay định nghĩa các gate **nội bộ**, và không cần chờ hoàn thành gate để chỉ đạo dự án. Việc miễn gate không tạo quyền nền tảng hoặc xác nhận một sự kiện thực tế chưa có.

## K-15. Validation programmes: decision support, not universal blocker
Các phương pháp đánh giá `Operational E2E`, `Commercial Validation`, `Creative Benchmark`, `Multi-channel Test`, QA blind review và protocol 24 briefs đều **tùy chọn theo mục tiêu nghiên cứu/Human**. Không có creative benchmark hay pilot bắt buộc trước khi bật, thử nghiệm, thay thế hoặc làm mặc định một Creative Reasoner, media provider hay chiến lược nội dung. Human có thể lựa chọn, giảm, bỏ hoặc thay phương pháp xác minh nội bộ. Nếu chưa có kết quả thử nghiệm thực, mô tả trạng thái chưa được thử, không được quảng bá như đã benchmark/đã kiếm tiền. Để khẳng định một quyền publish hay outcome đã xảy ra, sử dụng bằng chứng thực tế của bên ngoài.

# PART III — ENGINEERING & ACCOUNTABILITY

## K-16. Change / work item contract
Minimal `WorkItem`: `work_id`, `source_decision`, `objective`, `scope_in/out`, `affected_modules`, `expected_artifacts`, `acceptance_tests`, `side_effect_class`, `budget_envelope_ref?`, `evidence_required`, `owner`, `status`.  
Artifact states: `PROPOSED → ACCEPTED_FOR_WORK → IMPLEMENTED → VERIFIED → (LIVE_CONFIRMED where relevant)`. `DESIGN_ADDRESSED` is not implementation; local test PASS is not live account authorization. No implicit dispatch, TASK numbering or lifecycle imported from AIOS or Python Agent. Dispatch and code-write mechanisms are selected later by AI Commerce's own project decision.

## K-17. Failure / handoff / definition of done
**Mặc định gợi ý** các job có thể lưu `stage`, `error_code`, `scope`, `attempt_id`, `last_known_state`, `evidence/log_refs` và `next_action` để sửa dễ. Human có thể yêu cầu lược bỏ hoặc thay toàn bộ biểu mẫu, kiểm thử và điều kiện Done cho công việc **nội bộ**, không cần hồ sơ ngoại lệ trước. Đối với media sáng tạo, không bắt buộc source pack, claim map, storyboard đủ trường, blind review, minimum variation, creative QA hay số lượng concept. Khi xác nhận một phát hành/trả phí/thanh toán có thật, thông báo cần phản ánh provider receipt hoặc đúng trạng thái chưa xác minh.

**Negative-test catalogue (optional, chọn theo hành động):** quyền account/placement không hợp lệ; asset/license dùng ngoài phạm vi; claim factual sai khi quảng bá; private upload bị nhầm public; retry gây đăng trùng; token hết hạn; wrong account; dữ liệu missing giả là zero; payout pending giả settled; cost timeout gây charge trùng; nội dung ngoài đưa lệnh; quyền bị revoke. Đây là gợi ý kiểm tra cho hành động cụ thể, **không chặn sáng tác/render media**.

## K-18. Technical architecture / provider choice
**Initial suggestion, not constitutional choice:** Python modular monolith or CLI/API, typed schema, job manifest, durable local state, FFmpeg render, hash/artifact ledger, provider-neutral LLM/video/TTS adapters, CSV/manual reporting and assisted/manual publishing. Introduce DB/queue/UI/cloud/multi-agent only when an evidenced need outweighs complexity. Every provider choice has ADR for capability, commercial rights, cost/usable output, reliability, rate limits, data handling and exit route. No implicit Playwright/browser account coupling to Python Agent.

## K-19. Human Decision Register
Track `OPEN | DECIDED | SUPERSEDED` for: first niche, channel, account identity, risk envelope, assisted vs direct publishing, real footage/license, actual report access, scale threshold, own-brand capital. Leaving them OPEN is valid for architecture research; side effects depending on them remain unauthorized. Old budgets or historical pilot examples are not granted to this project.

## K-20. Versioning, amendments, canonical activation
Human có thể thay mọi Contract/Constitution/ADR/workflow/schema bằng chỉ thị trực tiếp; **hiệu lực ngoại lệ nội bộ không phụ thuộc** việc sửa file, làm amendment, ghi Decision Register hoặc migration trước. Việc cập nhật source, diff/version, compatibility và decision log được thực hiện sau nếu cần minh bạch/duy trì code. Canonicalization vào repository độc lập là quyết định riêng của Human; chỉ thị sửa governance lần này **chưa** tự là commit, dispatch, publish hay tạo repo. Một external action cần phản ánh đúng quyền provider và kết quả thực tế, không được sinh trạng thái PASS bên ngoài bằng amendment nội bộ.

## K-21. Acceptance verdict envelope
Each Product Review reports these independently:
```
OPERATIONAL_E2E_STATUS = PASS | FAIL | PARTIAL | NOT_TESTED
COMMERCIAL_ACCESS_STATUS = ELIGIBLE | INELIGIBLE | UNKNOWN | EXPIRED | REVOKED
PUBLICATION_STATUS = PUBLISHED_CONFIRMED | PRIVATE_ONLY | SUBMITTED | MANUAL_ACTION_REQUIRED | UNKNOWN | REJECTED | REMOVED | NOT_ATTEMPTED
ATTRIBUTION_TIER = A | B | C | D | NOT_AVAILABLE
SETTLEMENT_STATUS = SETTLED | PARTIAL | PENDING | UNAVAILABLE
CONTRIBUTION = amount | null  + currency + included_cost_scope
COMMERCE_VERDICT = VALIDATED_FOR_SCOPE | PROMISING_DIRECTIONAL | NEGATIVE | INCONCLUSIVE | NOT_TESTED
NEXT_ACTION = actor + bounded action + evidence dependency
```
No rolled-up `SUCCESS=true` may erase a failed/missing sub-verdict.

## K-22. Traceability back to Blueprint AC-01…AC-25
- Audience/commercial flow: AC-01,02,05,06 (§§3,5,15).
- Source/rights/claims/policy: AC-03,04,08,19,20,23,24 (§§4,9,12).
- Media pipeline and QA: AC-06,07,08 (§§7,9).
- Distribution and side effects: AC-09,10,11,12,21 (§§11,19).
- Metrics/economics/learning: AC-13…18 (§§13–16).
- Semantic ownership/own brand: AC-22,25 (§§2,6,21).

## K-23. Explicit non-goals of v0.1
Không có lời hứa dự báo viral chắc chắn, không tạo báo cáo doanh thu/hoạt động nền tảng sai sự thật, không tự nhập hàng hoặc tự cho ngân sách ngoài mandate, không tự nhập Python Agent/AIOS. **Không có non-goal mang tính cấm media creative**: thử nghiệm lượng lớn, tái sử dụng motif có chủ đích, template, AI-generated, meme, quảng cáo trực diện hoặc các format mới đều có thể được Human chọn trong phạm vi quyền thực tế của từng nền tảng.

## K-24. Shared schema & domain event contract
Mỗi record type và event phải có `schema_id/version`, `record_id`, `domain_owner`, `source_refs`, `observed_at`, `effective_at?`, `actor/attempt_id`, `payload_digest` và `evidence_status` theo phạm vi. Event loại `HYPOTHESIS_PROPOSED`, `CLAIM_VERIFIED`, `RENDER_COMPLETED`, `PUBLISH_SUBMITTED`, `PUBLISH_CONFIRMED`, `LINK_REVOKED`, `MEASUREMENT_IMPORTED`, `SETTLEMENT_RECONCILED` không thể tráo ý nghĩa cho nhau. Consumer đọc immutable version; nếu mismatch báo lỗi rõ và lựa chọn migration. Identity external vs internal tách trong data dictionary. Không tạo giant shared mutable state giữa mọi module.

## K-25. MeasurementPlan & Reporting provenance contract
`MeasurementPlan`: `plan_id`, `campaign/creative refs`, `primary_metric`, `denominator`, `metric_definition_version`, `platform/account`, `cohort`, `observation_window/timezone`, `source/export`, `missingness`, `attribution_tier`, `settlement_lag`, `cost_scope`, `review_at`. Mỗi `PerformanceObservation` giữ raw value, unit, data origin, collection date, metric status và transform version. Khi platform đổi cách đếm (vd. YouTube notice 27/08/2026), cần explicit comparability check. TikTok Creator Center export có thể tồn tại theo tài liệu nhưng entitlement/schema của account được xét riêng. Tier A/B/C/D áp dụng đúng nguồn; không tạo joined event ID hay per-video profit không được trả.

## K-26. LegalPolicy / Revocation contract
`LegalPolicySnapshot`: `{jurisdiction, activity, actor_role, account, platform, category, source_version, effective_from, checked_at, applicability_status, disclosure, claims, media_rights, ai_label, data_processing, takedown, reviewer}`. `Applicability` không được suy chỉ từ tên một đạo luật. Policy sources kiểm ngày 27/10/2026 gồm 122/2025/QH15, 75/2025/QH15 và **248/2026/NĐ-CP**. `REVOKED` rights/link làm hold ảnh hưởng mới, tìm các publication/asset liên quan, tạo action/receipt hoặc manual handoff; operator không có quyền thì `REMOVAL_UNVERIFIED`, không ghi completed. Đối với nội dung thương mại, giữ bằng chứng disclosure và actor consent.

## K-27. Claims, negative test matrix & readiness tracking
Khi công bố kết quả kỹ thuật, nên giữ rõ `DESIGN_RECONCILED | IMPLEMENTED | BUILD_VERIFIED_FOR_SCOPE | LIVE_CONFIRMED` và quyền thực tế chưa biết. Bộ negative tests là menu theo risk surface, không phải quota test hay creative gate. Human có thể thay đổi cả readiness states lẫn yêu cầu kiểm nội bộ mà không cần amendment trước; trạng thái bên ngoài chưa được xác minh phải được mô tả đúng là chưa được xác minh, bất kể giản lược nội bộ.

## K-28. Own-brand decision interface
`OwnBrandDecisionBrief` chứa demand proof, affiliate cohort limitations, differentiated offer, supplier quality/lead time, MOQ, COGS, fulfillment, refunds, inventory aging, base/upside/downside scenarios, cash at risk, break-even và `human_decision_ref`. Một brief có thể khuyến nghị no-go; nó không cấp purchase order. Ngân sách hàng tồn kho là envelope Human mới, tách biệt hoàn toàn ngân sách affiliate/AI-media.

## K-29. Policy freshness and platform-specific exceptions
Commercial/publish actions cần fresh capability validation theo `account×app×market×surface×scopes×timestamp`; privacy và commerce link là independent fields. TikTok Shop Adjustment Program và creator 1000+ là **đường chính sách khác nhau**, không code một ngưỡng followers mặc định. TikTok unaudited Direct Post private-only là policy-level; app nội bộ không tự suy ra eligible. YouTube documentation conflict được giữ `POLICY_CONFLICT_REQUIRES_APP_VERIFICATION` đến khi có account/app evidence. Không dùng browser workaround để lách TOS. Source register thuộc Blueprint §38.

## K-30. Governance consistency and activation checklist
Pack gồm Blueprint v1.2 **giữ nguyên là tài liệu nghiên cứu tham chiếu**, Manifesto v0.3, Constitution v0.3, Project Contract v0.3, RA findings và Decision Register. Human có toàn quyền sửa/miễn trừ toàn bộ quy định nội bộ ngay bằng chỉ thị. Nếu Human chọn canonicalize, Human chỉ định repository AI Commerce độc lập và bản văn được chọn; không có thủ tục amendment trước như điều kiện cho quyền Human. Cho đến khi có hành động trên repository thực, trạng thái tài liệu là `PROPOSED`; không tuyên bố đã ghi GitHub, dispatch, publish hay có quyền account thực tế. Không có review/approval/benchmark/QA bắt buộc cho việc sáng tác creative theo governance mặc định.

