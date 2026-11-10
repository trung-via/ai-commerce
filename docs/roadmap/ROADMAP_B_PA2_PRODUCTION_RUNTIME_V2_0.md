# Roadmap B v2.0 — PULSE: Production Runtime độc lập, Brain dẫn dắt (PA2)

**Ngày thiết kế:** 2026-11-04. **Trạng thái:** CANONICAL PROPOSED IMPLEMENTATION ROADMAP cho **PA2 đã được Human chọn** trong ChatGPT. Chấp thuận **hướng kiến trúc** không đồng nghĩa chấp thuận mọi work package, lịch chạy, provider, chi phí, tài khoản hay phát hành bên ngoài. B0.1 là lần cắt **SOURCE_ONLY_DESIGN**, không phải production runtime đã hoạt động.

**Nguồn đóng băng:** `trung-via/ai-commerce@26398290f8c2440eaa383783dbdf84f8d95610ab`. HEAD local đúng SHA này, tree sạch và cả 12 đường dẫn được phép đều chưa tồn tại trước khi viết. Đây là kiểm tra checkout, không phải lời xác nhận remote `main` không thể thay đổi sau đó. Có source/path drift thì HOLD; không ghi đè source cũ hoặc sửa workflow bảo vệ.

Thẩm quyền lựa chọn đến từ chỉ thị Human ngày 2026-11-04 trong hội thoại hiện tại, được chép có giới hạn trong [WorkItem B0.1](../../work_items/B0-1-PA2-DESIGN-001.json). Chưa có independent Brain review, PR CI hay post-merge acceptance cho thay đổi này. PA1 mở rộng EF02 thành product scheduler đã được cân nhắc và **không được chọn**. PA2 dùng EF01/EF02 để xây/sửa phần mềm; một process production riêng sẽ chạy product journey. Brain là **ChatGPT chat hiện tại**, không phải OpenAI API đóng vai thay thế.

## Quy ước tên sản phẩm — Human chọn PULSE sau candidate B0.1

**Tên ngắn chính thức theo quyết định Human trong ChatGPT ngày 2026-11-04: PULSE (AI Commerce Production Runtime).** PULSE là runtime production độc lập; không phải tên mới của EF01/EF02, ChatGPT Brain hoặc một hệ thống thứ hai. Đây là cập nhật tên gọi đối với PA2 đã chọn, không cấp phép triển khai live hay tăng quyền.

- **PULSE Core:** state machine, scheduler, transaction và recovery.
- **PULSE Ledger:** coordination journal, lease, checkpoint, action/effect intent và evidence refs; không thay các owner business ledger của Roadmap A.
- **PULSE Studio:** các tool media, FFmpeg/voice/subtitle, provider-capability adapters và technical QA.
- **PULSE Bridge:** giao tiếp directive/review packet giữa ChatGPT Brain và PULSE, mặc định Human-attended; exact-chat CDP/Wake chỉ là lựa chọn tương lai chưa được nghiệm thu.
- **PULSE Adapters:** adapter A06/A08/A09 và, khi có quyền riêng, external media/social/analytics/product provider.
- **EF01/EF02:** chỉ xây dựng, kiểm thử, sửa source PULSE; không phải scheduler cho video thường lệ.

Tên kỹ thuật package `ai_commerce.production_runtime`, ba JSON Schema và các owner tích hợp hiện có **được giữ nguyên** trong B0.1 để tránh thay đổi contract chỉ vì đổi nhãn hiển thị. Không diễn giải tên PULSE là một implementation hoặc runtime live đã chứng minh.

## 1. Mục tiêu và ranh giới chung

Mục tiêu nhỏ nhất là một hành trình độc lập có thể nhận creative directive → chạy công cụ được chỉ định → QA kỹ thuật → dừng `WAIT_BRAIN` → nhận quyết định Brain có kiểm chứng → bàn giao draft hoặc đợi quyền phát hành riêng → nhập quan sát đúng nguồn → học và gửi brief tiếp theo cho Brain. Executor sản phẩm thực thi chiến lược đã chỉ định, không tự quyết định creative strategy, chọn thị trường, đổi thông điệp hay tự tăng ngân sách. Codex/Antigravity chỉ làm engineering change hoặc repair ngoại lệ có scope riêng. Không gọi coding agent cho mỗi video thường lệ.

Trong B0.1 chỉ có ba contract draft, validator không I/O, reducer thuần và tài liệu. SQLite, lease, worker, scheduler, provider transport, UI, Wake và credentials **chưa được implement ở đây**. Các bước tiếp theo là đề xuất có điều kiện; roadmap không tự dispatch chúng.

Không có automatic multi-agent swarm. Có thể nghiên cứu phối hợp nhiều agent sau khi đo được lợi ích cụ thể, có quyền riêng và chi phí rõ; không tạo một agent/microservice/DB cho mỗi owner. Không dựng customer testimonial, doanh số, reach, audience growth, provider receipt hay ROI để lấp chỗ thiếu bằng chứng.

## 2. Năm semantic owner và tái sử dụng Roadmap A

Theo [semantic owners](../engineering/SEMANTIC_OWNERS.md), runtime chỉ giữ điều phối và tham chiếu; **LINK BY SHA/REF ONLY** tới ledger/snapshot của owner, không tạo authoritative commerce ledger thứ hai.

| Owner | Truth tiếp tục sở hữu | Runtime được phép giữ |
|---|---|---|
| Commerce Opportunity | Trend/audience/opportunity, portfolio, own-brand hypothesis | Ref/SHA tới A10/A18, yêu cầu Brain xem xét |
| Product Intelligence | Identity/variant, facts/claims/offers, asset rights | Ref/SHA A12; trạng thái stale/revoked không được nâng cấp |
| Media & Creative | Ý tưởng/script/assets, creative hypothesis và ý nghĩa learning | Directive version/digest, QA và artifact refs A03–A06/A11/A17.1 |
| Distribution | Account/surface eligibility, publication/link, raw metrics | A07/A08/A09/A14/A17 refs, scope và effect intent |
| Commerce Operations | Product jobs/custody, actual costs, retry và settlement reconciliation | Coordination journal O01/O02; A15/A16 vẫn authoritative cho tiền/retry evidence |

Engineering Foundation sở hữu build/repair, không là owner thứ sáu. Human giữ quyết định cao nhất; waiver nội bộ không tạo quyền của nhà cung cấp hoặc chủ tài sản.

[Roadmap A A01–A18](ROADMAP_A_CREATIVE_FIRST_V1_0.md), [Work Tree v1.1](WORK_TREE_DETAILED_V1_1.md) và [63 WBS máy đọc được](WORK_TREE_V1_1.json) vẫn là bản đồ nguồn. Roadmap B sắp thứ tự **đưa các capability vào hành trình vận hành**, không tuyên bố mọi AC live của A đã hoàn thành. Những status lịch sử ở cuối tài liệu A phải đọc theo ngày và bằng chứng, không lấy một dòng PASS offline làm readiness live.

| Nguồn A / WBS | Capability tái sử dụng → consumer B | Giới hạn phải giữ |
|---|---|---|
| A01 G01/G02/G03/G06/G08; A02 G04/G05/G07 | Foundation/contracts/provenance → B0-S, B0 | EF01/EF02 engineering lane; không product scheduling |
| A03 C01/C02 | `creative/live.py`/briefs → B1 directive và tool plan | Client seam có authorization, chưa chứng minh model thực được cấu hình |
| A04 O01–O04/C04 | `media/generation.py`, operations generation → B0/B1 job và artifact adapter | Async contract không phải daemon; UNKNOWN không retry mù |
| A05 C03/C06; A06 E01 | Composition/render và `e2e/service.py` → B0 offline delivery, B1 creative QA | Stills→MP4 không chứng minh video generation; generation_performed=false vẫn false |
| A07 D01; A08 D02/D03/D04 | Capability và `distribution/assisted.py`/receipt → B2 | Export khác submitted, confirmed khác public; offline ALLOW không entitlement |
| A09 L01/L02/E02 | `analytics/importer.py`/matching → B2 measurement, B3 | Manual export là HUMAN_SUPPLIED; missing khác 0; source/window/metric không được mất |
| A10 M01–M05 | Signal/opportunity/brief bridge → B1/B3 | Giả thuyết khác trend đã chứng minh, có thể content-only |
| A11 L03/L06 | `learning/core.py`, registry/report/export → B3 | Correlation khác causation; không tự đổi creative strategy |
| A12 P01/P03/P04/P05/P06 | Product evidence/rights → B1 tùy chọn, B4 | Seller-asserted khác verified; SKU không là gate cho creative |
| **P02** authorized source adapters (gap của cut A12); **P07** freshness/revocation | B4.1 source-specific adapter → A12; B1/B2/B4 recheck rights/facts trước external action | Không mặc nhiên có API/extraction rights; stale/revoked phải liệt kê affected refs |
| **C05** voice/music/subtitles (gap độc lập) | B1.3 audio/subtitle adapter → C06/QA | Silence hợp lệ; cần đo tiếng Việt, sync và music/voice rights |
| A13 C08/C09/D06/E03 | Technical/claim/rights/placement evidence → B2/B4 release gate | Fiction không bị đổi thành factual claim; UNKNOWN chỉ chặn action liên quan |
| A14 L04/E04 | `affiliate_attribution/evidence.py`, affiliate commerce → B4 | Attribution tier A/B không xác thực giao dịch, payout hoặc causal profit |
| A15 O05/O06/O07 | `operations/retry_guard.py`/cost/status → B0 recovery, B1/B2/B4 | Runtime không ghi thêm sổ tiền; supplied reconciliation không thành provider attestation |
| A16 L05/E05 | `settlement/review.py` → B4 economics | Estimated/charged/settled/refunded riêng; không cộng khác currency ngầm |
| A17 C07/C10/D05/D07/D08/L07/E06 | Variants/learn-back, reconciliation/dashboard, `channel_adapters/direct_post.py` → B2/B3 | OfflineDirectPostAdapter mô phỏng; synthetic confirmation không tạo real post |
| A18 M06/M07/E07 | Portfolio/own-brand/`opportunity/scale_option/memo.py` → B3/B4 | Advisory scenario, không demand proof, campaign change, capital hoặc inventory commitment |

Các consumer trong manifest B0.1 là **ranh giới tích hợp dự kiến và regression closure đọc-only**. Chúng chưa import package `production_runtime`; B0.1 không nối adapter bằng cách sửa chúng.

## 3. Critical path và cổng chứng cứ

`B0.1 source design → B0.2 journal/recovery offline → B0.3 worker + attended WAIT_BRAIN → B1 tool capability → B2 assisted measurement → B3 verified learning`. B4 là nhánh tùy chọn, không gate creative. `B0-S public/private custody → riêng từng external effect ở B1/B2/B4`; B0-S không chặn validator/offline source work được ủy quyền.

Engineering: WorkItem scope → EF01/EF02 build → MT-01 owner+consumer tests trên Windows self-host, **12 workers thực mỗi nhóm** → trusted EF02 worker tạo một candidate → independent PR CI Windows hosted **4 workers** → genuine independent Brain PASS bind **exact HEAD/base**, trusted auto-merge. Không có second Human merge queue. Executor không tạo PR, merge, ref, retry bằng Git khác hoặc thay protected registry. CI green và executor tự đánh giá không thay independent Brain PASS.

Production: Human-attended receipt là default được hỗ trợ về thiết kế: Brain xem evidence packet trong **đúng chat hiện tại**, Human chuyển receipt có kiểm chứng tới private control. Chưa có identity verifier implementation trong B0.1; một JSON có actor=CHATGPT_BRAIN hay `verified=true` từ event không là thẩm quyền. Future verified CDP/Wake là **OPTIONAL**, không prerequisite và không được claimed. Không dùng Chrome CDP/HostOps để hoàn thành B0.1.

Mỗi phase dưới đây dùng cùng nguyên tắc MT-01: chỉ nhóm có mapping rõ, ghi EXECUTE/REUSE/INVALIDATED/UNPROVEN; changed bytes/deps/test definitions làm invalidate. Không global full suite trừ registered requirement chỉ đích danh; thiếu mapping thì HOLD. Repair cùng engineering action tối đa **2 vòng**, giữ cả FAIL/UNKNOWN, timeout không được biến thành PASS. Hosted chỉ Windows; 12 self-host khác 4 hosted, khác số engineering actions.

## 4. B0-S — Public source / private control và authorization

**Mục tiêu:** có trust boundary kiểm chứng được trước khi bất cứ private credential hoặc external action nào được mở. Public GitHub `trung-via/ai-commerce` chỉ chứa code/schema/test giả lập và receipt đã khử nhạy cảm; private control/worker không tin public Issue/artifact như lệnh có thẩm quyền.

**Work packages đánh số:**

1. **B0-S.1 (G02/G05/G08):** lập inventory luồng public CI → EF02 coding runner → private production; phân loại dữ liệu, ref công khai/riêng, actor và nguồn source SHA; xác nhận private control custody riêng. Dùng HostOps W1 như threat model tham khảo, không import quyền W1.
2. **B0-S.2 (G04/G07/O06):** thiết kế và review enrollment trust root, authenticated attended Brain receipt, Human per-action release/spend grant, provider/account/surface entitlement, TTL/revocation. Bind journey, directive digest/version, source SHA, final asset SHA, exact action scope, ceiling/currency; UNKNOWN/HOLD mặc định.
3. **B0-S.3 (G05/G08):** tách execution/credential custody khỏi public runner cùng Windows SID. Một tài khoản Windows hiện có không tự tạo isolation; ACL/DPAPI/Credential Manager không đủ trước malicious same-SID code. Chọn attended ephemeral custody hoặc external broker sau independent proof; chưa chứng minh thì không always-on private production.
4. **B0-S.4 (G06/O04):** redact evidence export và thực hành revoke/KILL, stale source, stolen receipt, scope tamper, credential theft/rollback threat bằng fixture; kiểm tra public Issue/artifact chỉ nhận metadata an toàn. Không đọc secret thật để chứng minh source design.

**Dependency/critical path:** đọc source/HostOps → S.1/S.2 review → S.3 custody evidence → entitlement live cho đúng action. S.4 phải đạt trước live; offline B0 không cần credential.

**In/out:** in là policy/contract/offline verifier plan; out là signer enrollment thật, token persistence, Windows service, HostOps dispatch, laptop write hay CDP/Wake. Source approval không là GO_SPEND/GO_PUBLISH.

**AC dương đo được:** inventory đủ 3 trust zones; mỗi effect loại được map tới 4 lớp quyền riêng (Brain strategy, Human grant, provider entitlement, rights); tất cả receipt bind exact source/scope/expiry. **AC âm:** 0 private tokens/cookies/raw customer data trong public source/issues/artifacts; 100% case wrong SHA/actor/chat/expiry/scope, fixture-as-authority và same-SID custody claim bị HOLD/deny. Không nhận “host secure” chỉ từ JSON claim.

**Windows tối thiểu và proof:** focused contract/redaction/authorization owner tests `-n 12`, consumer gate tests nếu thay gate; sau đó independent Windows hosted `-n 4`. Fixtures chỉ chứng minh denial logic. Custody live cần independent attack/revocation observation ngoài public SID và riêng Human authorization, hiện UNKNOWN.

**Brain/Human gates:** Brain review threat model; Human chọn attended hay isolated broker và duyệt exact external credential/effect riêng. **Rollback:** đóng ingress, revoke grants/token ở authority thật, không xóa journal; giữ UNKNOWN để reconcile, quay lại attended draft-only.

**Ước lượng giả định:** 1 engineer, 4–8 ngày công 6h/ngày; phí external ở cut source = 0; giả định trần thử custody $0–20 chỉ là đề xuất chưa cho chi. External isolation/provider review có lead time chưa biết. **30/60/90 có điều kiện:** ngày 30 inventory + denial fixtures; ngày 60 chỉ thử custody nếu Human duyệt và isolation có bằng chứng; ngày 90 vẫn draft-only nếu chưa chứng minh, không tự hạ gate để đúng lịch.

## 5. B0 — Minimum durable journey chạy độc lập

**Mục tiêu:** process Python Windows riêng chạy một journey offline có persistence và recovery, không dùng EF02 DAG/watch làm scheduler sản phẩm. B0.1 hiện tại mới là nền thiết kế thuần; các mục SQLite dưới đây thuộc **B0.2 trở đi**.

1. **B0.1 (G02/G04/G05/G06/G08):** canonical proposed roadmap, ADR, ba closed-schema draft `CreativeDirective.v1`, `BrainDecision.v1`, `ProductJourneyReceipt.v1`; dataclass bridge/reducer deterministic không I/O; manifest MT-01 và scope 12 file add-only.
2. **B0.2 (O01/O06/G05):** SQLite transactional append-only coordination intent/event journal, single writer và fencing lease. Unique journey/event/effect semantic key, expected sequence CAS; reserve intent transaction trước effect, observed result transaction sau effect; persist ref/SHA tới A15/A08, không ghi duplicate commerce facts/costs. Crash recovery và ledger-ref reconciliation có protocol review trước adapter live.
3. **B0.3 (O02/O03/O04/E01):** worker attended/manual CLI **riêng**, input file riêng tư và known tool allowlist; chạy adapter offline A06 → technical QA → durable `WAIT_BRAIN`; resume bằng receipt bind exact bytes; reboot/process kill không mất pending/release state. Không background install trong source cut này.
4. **B0.4 (O07/E01):** sanitized journey evidence packet, Human-readable hold reason và next action, export draft/learning branch không publish; count recovery results, attempts, elapsed/active time và coding-agent invocations.

**Dependency/critical path:** B0.1 → schema freeze/review → B0.2 transaction + crash tests → B0.3 independent worker → B0.4 offline acceptance. B0-S chỉ cần hoàn tất trước custody/live effects; A06 FFmpeg availability được kiểm đúng host, không suy từ API tồn tại.

**In/out:** in là durable execution tối thiểu 1 writer/1 journey, product_ref=null, Human-attended WAIT_BRAIN và offline delivery; out là provider SDK/network, publish, production credentials, Temporal/LangGraph dependency, autonomous creative strategy hoặc automatic swarm.

**AC dương:** 1 offline journey mới và reopen/resume cho cùng ID trả đúng state/artifact refs; 1 crash tại mỗi cut reserve-before-call / after-call-before-receipt / receipt-committed có recovery quyết định rõ; 2 process tranh lease chỉ 1 writer nhận quyền; tất cả events có stable ID và sequence. Các chỉ tiêu này là **planned AC B0.2/B0.3**, không quan sát B0.1. B0.1 AC riêng: ba schema đóng, reducer dương/âm deterministic, 12 additions và zero edits source cũ.

**AC âm:** 0 model/provider calls, 0 Git writes từ product execution; event không tự skip WAIT_BRAIN; 0 replay non-idempotent effect khi crash/UNKNOWN, kể cả rekey; missing/corrupt/rollback journal → HOLD; two authoritative cost/settlement stores → reject design. Coding-agent calls mỗi routine journey có **target 0**, chưa có journey benchmark quan sát.

**Windows tối thiểu/proof:** B0.1 owner `tests/test_b0_production_contracts.py` và registered A03/A06/A08/A09/A15/A17/A18 consumer closure `-n 12` mỗi group. B0.2 thêm transaction/lease/fault-injection scoped tests, B0.3 actual process kill/reopen và A06 fixture MP4; giữ FAIL/UNKNOWN. Offline crash/reopen không chứng minh power-loss trên mọi disk, actual provider at-most-once hay secure unattended custody.

**Brain/Human gates:** independent Brain acceptance source exact HEAD/base trước trusted auto-merge; Human không phải approve merge lại bằng queue thứ hai. Việc bật private worker hoặc quyền ngoài source cần scope riêng. **Rollback:** dừng writer/lease acquisition, preserve append-only journal/backup/fencing history, verify schema compatibility, dùng draft thủ công; không reset database để replay pending.

**Giả định:** B0.1 2–4 ngày công; B0.2/B0.3 5–10 ngày công, 1 engineer; external fee $0 cho offline, chi phí engineering chưa định giá; không coi những khoảng này là benchmark. **30/60/90:** ngày 30 B0.1 được review và B0.2 proof nếu không có drift; ngày 60 journey độc lập offline nếu recovery PASS; ngày 90 harden theo lỗi quan sát, vẫn HOLD ở WAIT_BRAIN nếu chưa có verifier thật.

## 6. B1 — Creative/media thực và freeform product_ref=null

**Mục tiêu:** biến directive của Brain thành media artifact qua capability thật được phép, chứng minh generation và chất lượng kỹ thuật mà không ép SKU/storyboard/creative quota. Executor chỉ chọn tool từ plan được duyệt; tool failure/creative ambiguity trả lại Brain.

1. **B1.1 (C01/C02/C03/M05):** adapter directive → A03/A05 editable brief/script; nội dung fiction/humor/meme/animation/content-only hợp lệ, product_ref=null. ChatGPT Brain quyết creative strategy; optional generation model chỉ làm tool task rõ scope, không nhận vai Brain thay chat.
2. **B1.2 (C04/O03/O05/O06):** chọn duy nhất provider/model ban đầu sau capability/rights review; map submit/observe/fetch/reconcile A04 vào journal riêng, provider request ID và output hash, usage/cost refs A15. Fixture, Human import và provider generation không được tráo nhãn; timeout giữ UNKNOWN.
3. **B1.3 (C05/C06/C08):** voice/music/subtitle tùy chọn, video không lời là positive path. Kiểm dấu tiếng Việt, timing/duration, audio clipping và subtitle sync; license/voice consent gắn đúng asset. Reuse compose/render/technical inspection A05/A06/A13, không dùng thẩm mỹ của executor làm veto chiến lược.
4. **B1.4 (P01/P03–P07/C09):** nếu Human chọn sản phẩm, consume A12 facts/claims/rights by ref; lập freshness/revocation adapter P07 và affected asset list. P02 provider-specific product source để B4, không biến product source access thành gate creative thuần.
5. **B1.5 (C07/O07):** nhỏ nhất một usable generated asset + receipt + draft render, evidence packet cho Brain; thay final asset/directive version phải review lại digest scope trước release, không silently đổi bytes sau approval.

**Dependency/critical path:** durable B0 + external gate B0-S → chosen provider authorization → B1.2 generation → B1.3 QA → Brain review. B1.1/offline C05 có thể xây trước quyền API; product lane độc lập/tùy chọn.

**In/out:** in là provider-neutral tools, async observation có bounds, optional audio, truthful cost; out là multi-provider rollout mặc định, self-selected strategy, live publish/paid ads, copy OpenMontage AGPL source hoặc coding-agent orchestration thường lệ.

**AC dương:** sau authorization riêng, ≥1 real request có provider-bound evidence/request ID, fetched output SHA và usable QA result; ≥1 freeform null-product journey, ≥1 silence render; có cost status charged/estimated/pending/unknown riêng. **AC âm:** 0 fixture→provider provenance upgrades; unconfigured model → NO_MODEL_CONFIGURED/hold; expired rights/P07 revocation chặn affected external release, không chặn unrelated creative; spend vượt ceiling và UNKNOWN retry bị deny 100% negative fixtures.

**Windows tối thiểu/proof:** scoped A03/A04/A05/A06/C05 tool tests `-n 12`, seeded failure adapters, wrong digest/codec/rights/cost/replay; hosted Windows `-n 4` sau candidate. Offline test không được gọi paid APIs. Live receipt phải do authenticated provider verifier riêng kiểm; user upload chỉ HUMAN_SUPPLIED. Chưa có real provider proof ở B0.1.

**Brain/Human gates:** Brain xác nhận directive + usable creative; Human duyệt exact provider, purpose, cap/currency/TTL và rights; không lấy architecture approval làm spend grant. **Rollback:** ngắt submit adapter, giữ provider IDs/intents/cost refs, reconcile job đang chạy, quay về import local/draft; không trả tiền lần hai để “sửa” UNKNOWN.

**Giả định:** 7–15 ngày công + provider access lead time UNKNOWN; đề xuất test envelope $0–50, chưa cho chi, không là báo giá. **30/60/90:** ngày 30 chọn capability và offline tool plan; ngày 60 một provider proof chỉ khi gate mở; ngày 90 tăng reliability/usable-yield theo dữ liệu, không mở thêm provider chỉ để đạt timeline.

## 7. B2 — Assisted-first distribution và measurement

**Mục tiêu:** bàn giao draft rõ ràng cho Human trước; chỉ đo publication và metrics đúng nguồn, đúng account/surface/window. Assisted-first không hàm ý runtime được đăng bài.

1. **B2.1 (D01/D02/D03/D04/C09):** reuse A07/A08 capability/export + immutable caption/video hashes; approve final asset and exact account/surface, claim/rights/disclosure cho action liên quan. Human thao tác provider UI khi đã có quyền; runtime ghi supplied receipt trung thực.
2. **B2.2 (D05/D07/D08/O06):** reconcile A17 observations, distinguish exported/submitted/confirmed/public/private/deleted/unknown; idempotent semantic target key chống rekey duplicate. Postiz chỉ là adapter riêng SaaS hoặc self-host tương lai, proof đầu **draft-only**; không deploy hoặc nhập tokens ở B0.1.
3. **B2.3 (L01/L02/E02):** A09 measurement plan/import mapping, raw SHA và metric definition/window, account/post/content matching; corrections append thay overwrite; missing/NOT_AVAILABLE khác observed 0.
4. **B2.4 (L07/O07):** read-only dashboard refs và measurement packet để B3; không tổng hợp “SUCCESS” chung cho render/publish/revenue.

**Dependency/critical path:** usable A06/B1 artifact + B0-S quyền → draft export → Human release/actual receipt → compatible metrics import → B3. B2.3 fixture implementation không cần publish live. Direct publishing chỉ xem xét sau assisted evidence và separate grant, không nằm critical path ban đầu.

**In/out:** in là export/manual handoff/reconciliation/import; out là default direct publish, login automation, cookies/CDP, paid reach, fabricated audience growth và Postiz deployment.

**AC dương:** 1 immutable draft package verify lại SHA; 1 supplied receipt hiển thị đúng provenance; 1 analytics import đúng window/source/metric, trace được về post hoặc account aggregate thật của nguồn cung cấp. **AC âm:** exported không tự thành submitted/public; synthetic A17 CONFIRMED vẫn synthetic; wrong account/window/duplicate và unknown field bị reject; 0 publishes nếu thiếu một trong Brain/Human/provider/rights gates; 0 external effects trong offline tests.

**Windows tối thiểu/proof:** A07/A08/A09/A17 owner+consumer tests, UNKNOWN/replay/public-visibility/missing-metric negatives `-n 12`; hosted `-n 4`. Assisted screenshot/URL do Human cung cấp là HUMAN_SUPPLIED, không provider-attested. Live confirmation/visibility cần verifier và bằng chứng độc lập; phải report partial nếu thiếu.

**Brain/Human gates:** Brain review ready-for-release package và measurement meaning; Human quyết phát hành cụ thể + provider entitlement; architecture/Brain approval không tự publish. **Rollback:** tắt adapter submission, revoke pending grants, giữ receipt; sửa/gỡ bài có external effect riêng cần grant mới, không giả “rollback local” là bài đã bị gỡ.

**Giả định:** 5–10 ngày công + access UNKNOWN; proposed testing cap $0–20, chưa cho chi; reach/revenue không dự báo. **30/60/90:** ngày 30 draft/reconciliation fixture; ngày 60 assisted release chỉ nếu tất cả gate có thật; ngày 90 measurement review nếu có export đủ chuẩn, nếu không ghi NO_DATA và quay về draft.

## 8. B3 — Verified learning → brief Brain / portfolio tiếp theo

**Mục tiêu:** dùng evidence đủ scope để Brain quyết brief tiếp theo; giữ uncertainty và counterevidence, không cho runtime tự biến correlation thành chiến lược hoặc outcome.

1. **B3.1 (L03/L06):** reopen A09/A11 observations, metric definitions, window, source SHA và revision lineage; gắn evidence quality và freshness, dedupe mẫu; user-supplied vẫn supplied.
2. **B3.2 (C10/E06/C07):** A17 learning bridge tạo proposal gồm lesson/counterexample/unknown → next Brain brief request. Brain trong current chat approve/revise/hold; không auto-modify prompt/portfolio theo unverified feedback.
3. **B3.3 (M06/M01–M05):** A18 portfolio recommendation links costs/performance; compare compatible cohorts/definitions, ghi coverage và reason code, không cộng data khác window hay currency tùy tiện.
4. **B3.4 (O07/E06):** quota/recovery dashboard: journey-level engineering calls, ChatGPT Brain turns, provider costs, quality/latency và lost work; báo cả missing counters. Quyết định có cần LangGraph chỉ sau complexity measurement.

**Dependency/critical path:** B2 measurement phù hợp → B3.1 evidence review → B3.2 attended Brain decision → next versioned directive; portfolio cần A10/A11/A15 source references, không cần own-brand. Fixture learn-back có thể xây trước, phải ghi mức synthetic.

**In/out:** in là verified-source traversal, advisory hypotheses và next brief; out là autonomous strategy, causal sales claims, automatic budget changes và multi-agent swarm mặc định.

**AC dương:** 1 lesson trace được metric/window/source và counterevidence; 1 next-brief packet có decision scope/version; ≥1 insufficient/incomparable example giữ UNKNOWN. **AC âm:** synthetic analytics không thành real growth; đổi metric definition invalidates comparison; 0 prompt/portfolio execution chỉ từ callback; no-data không nhận learning win; không fabricate customer outcomes.

**Windows tối thiểu/proof:** A09/A11/A17.1/A18.1 + affected consumer closure `-n 12`, fixtures stale/retracted/duplicate/incomparable/null-product; hosted `-n 4`. Offline next-brief packet không phải genuine Brain receipt; live current-chat attended turn cần independent authority receipt riêng. Không claims CDP/Wake.

**Brain/Human gates:** Brain quyết creative/portfolio recommendation; Human quyết thay campaign/budget nếu đề xuất; merge code theo independent exact-HEAD Brain gate chung. **Rollback:** append retraction/revision, invalidate dependent briefs/grants, không sửa lịch sử metrics; giữ bản directive trước đã review.

**Giả định:** 5–10 ngày công; external cap đề xuất $0, engineering/Brain quota giá chưa biết. **30/60/90:** ngày 30 lesson contract fixtures; ngày 60 1 evidence-linked proposal nếu data đủ; ngày 90 next brief/portfolio only sau Brain review; data thiếu thì ưu tiên đo tiếp thay tự kết luận thắng.

**B3.3 source contract (2026-11-10, Issue #747):** Khởi tạo consumer đọc-only `B3PortfolioReview.v1` nối B3.2 A09/A11/A17 pending với A18 M06 portfolio qua metric/cohort/window/timezone exact; tương thích ngữ cảnh **không** chứng minh chung native source/campaign. A18 vẫn sở hữu cost buckets theo currency, reason/missingness, provisional advisory. Chỉ source CI + Brain review độc lập mới cho phép ghi `MERGED`; không tự đổi budget, portfolio, chiến lược hoặc cấp quyền PULSE.

**B3.4 source contract (2026-11-10, Issue #752):** Chọn native B0.4 audit theo session/head SHA và A15 cost ledger entry IDs, trả `B3OperationalDashboard.v1` hash-bound, read-only. Cố định ranh giới quan sát: B0.4 chỉ audit phiên operator, A15 chỉ chi phí tự khai/synthetic theo currency. EF02 toàn hệ thống, Brain ChatGPT turns, Codex/Antigravity quota, Q4, lost work, billing/provider chứng thực và journey latency giữ `UNKNOWN/null` khi chưa có instrumentation. Không suy luận zero, ROI hay bật LangGraph. Đây là **source candidate**, cần Windows MT01 + Brain independent review/merge để được coi MERGED.

## 9. B4 — Affiliate/economics/own-brand tùy chọn, evidence-gated

**Mục tiêu:** chỉ đi nhánh thương mại khi Human chọn và evidence phù hợp; không buộc affiliate/own-brand để creative production được chạy.

1. **B4.1 (P02/P07/P01/P03–P06):** authorized product feed/API/extraction riêng provider, freshness/expiry/revocation propagation; không né provider rights/TOS, không mua quyền bằng schema flag. Cập nhật facts/claims/offer/rights A12 qua owner, runtime chỉ link ref/SHA.
2. **B4.2 (C09/D06/E03/E04/L04):** A13 eligibility + affiliate placement và A14 attribution; tách link active, publication, click, order, attribution tier; disclosure/claim rights và account×link entitlement trước placement thật.
3. **B4.3 (O05/O06/L05/E05):** A15 cost + A16 settlement reconcile exact currency/order scope; estimated/charged/pending và settled/refunded/revoked riêng. Không biến subtotal từ supplied exports thành realized profit đã xác minh.
4. **B4.4 (M06/M07/E07):** A18 scale/own-brand decision memo với demand evidence, COGS/MOQ/logistics/capital/downside; Human chọn research/hold/go scope riêng; inventory/order/payment luôn external commitment riêng.

**Dependency/critical path:** Human chọn affiliate + B0-S rights/custody + product/placement evidence → attributable observation → settlement/economics → optional brand memo. B3 portfolio research có thể không đợi settlement nếu chỉ đề xuất research với UNKNOWN economics rõ ràng.

**In/out:** in là evidence-gated advisory và adapter source được phép; out là auto affiliate activation, guaranteed commissions/profit, purchase stock, capital commitment hay live API spend không grant.

**AC dương:** 1 exact-source product pack có freshness/rights, 1 eligibility review đủ scope hoặc HOLD rõ, 1 commercial review tách unknown amounts và refund; nếu chọn own-brand có bear/base/bull assumptions + downside + missing evidence. **AC âm:** provider permission UNKNOWN → no commercial action; tier A/B không tự thành real order/payout; cross-currency sum/stale offer/revoked link bị reject/HOLD; 0 capital/inventory commitment từ memo.

**Windows tối thiểu/proof:** scoped A12/A13/A14/A15/A16/A18 tests `-n 12`, provenance/attribution/refund/freshness/rights/settlement negatives; hosted `-n 4`. Supplied export/fixture chỉ kiểm local replay. Actual order, paid/settled commission và provider charges cần evidence thực authenticated theo nguồn, không suy từ source PASS.

**Brain/Human gates:** Brain advisory economic/brand review; Human chọn branch, exact affiliate/provider/spend/release/capital scope và external rights; chưa có approval trong PA2 design request. **Rollback:** disable affected links/submission adapters và grants theo provider quyền thật, append revocation/correction tới owner, giữ unsettled evidence; không tự hủy order/refund/gỡ link nếu chưa được cấp quyền action đó.

**Giả định:** 10–25 ngày công nếu branch được chọn; access/settlement waiting time UNKNOWN. Provider thử nghiệm giả định $0–100, inventory/capital **không dự toán/không được cấp**. **30/60/90:** ngày 30 gap/rights/source review; ngày 60 chỉ affiliate evidence nếu Human chọn; ngày 90 economic/brand advisory nếu đủ actual evidence, nếu không giữ conditional research/HOLD. Không hứa doanh thu trong 90 ngày.

**B4.1a offline source cut (2026-11-10, Issue #757):** phát triển consumer đọc-only `B4FeedReadiness.v1` dùng A12 native registry/pinned head/pack export tại thời điểm đánh giá, phát hiện source/offer stale, rights scope expired, và counterevidence để yêu cầu review. Chỉ báo local `OFFLINE_ADVISORY_ONLY` hoặc `LOCAL_EVIDENCE_HOLD` đều **không** chứng nhận feed hiện tại, provider API/affiliate eligibility, quyền media, revocation ngoài hệ thống hay publish/spend. Đây là **candidate**, cần Windows CI + Brain PASS + trusted merge; live B4.1/P02 chưa được cấp phép.

## 10. Benchmark quota, chi phí và recovery bắt buộc ghi đúng mức

| Chỉ số | Định nghĩa và target đề xuất | Bằng chứng hiện tại |
|---|---|---|
| Coding-agent calls / routine product journey | **Target = 0**, gồm mọi Codex/Antigravity CLI/API/subagent invocation khi chạy journey; engineering build/exceptional repair ghi lane riêng, không giấu vào denominator | NOT_OBSERVED; B0.1 chưa có production journey benchmark |
| ChatGPT Brain turns | Đếm turn thực gửi/nhận trong đúng chat cho directive/review/learning, kể cả revise/HOLD; target giả định 2–4 turns/journey cần đo, không hard gate creative | UNKNOWN, không suy từ số event hoặc schema |
| Provider cost | Estimated/charged/pending/unknown, currency, request ID, usable asset, failed/unknown attempts; giá từ provider receipt khi có, không dùng quota coding agent làm tiền media | Không paid provider call ở B0.1; cost benchmark UNKNOWN |
| Recovery outcomes | Count resumed-without-effect, confirmed-by-reconcile, proven-no-effect/HOLD, unresolved UNKNOWN, duplicate denied, lost work; record actual crash cut, time và hashes | B0.1 chỉ pure reducer tests; durable recovery NOT_IMPLEMENTED |
| External rights | Ref/SHA, issuer, account/surface/asset scope, granted/restricted/unknown, expiry/revocation, independent verifier | Chưa enrolled/cấp quyền live |
| Journey quality/time | Wall time, active execution, WAIT_BRAIN/Human/provider time, usable outputs/request, QA reject/HOLD reason; không fabricate customer results | NOT_OBSERVED |

Đề xuất benchmark đầu B0.3: tối thiểu 1 journey offline + 3 crash cuts + 1 duplicate/lease conflict; B1/B2 chỉ đo live samples được Human cấp phép, không đặt creative quota. Receipt phải ghi command/source/files/dependency versions/platform/requested+actual workers và cả FAIL/UNKNOWN. Không tuyên bố tiết kiệm quota hay reliability trước khi đo. Các khoảng ngân sách ở từng phase **không cộng thành GO_SPEND**.

## 11. Tái nghiên cứu công nghệ và kế hoạch 30/60/90 tổng hợp

Ngày 2026-11-04 đã đọc nguồn chính thức để kiểm lựa chọn, không cài/chạy sản phẩm ngoài. [SQLite transaction](https://www.sqlite.org/lang_transaction.html) có một simultaneous writer; [atomic commit](https://www.sqlite.org/atomiccommit.html) có storage assumptions. Suy luận thiết kế: single-writer journal là điểm bắt đầu nhỏ, nhưng SQLite không giải quyết external exactly-once, credential custody hay anti-rollback. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) có checkpoint/resume và node re-execution; chỉ thêm adapter interrupts/checkpoint khi graph complexity được đo và dedup effects đã có. [Temporal Python](https://docs.temporal.io/develop/python) có workflow/activity/worker: defer đến lúc nhiều async workflows/long waits/host failures có số đo khiến own coordinator không còn đủ.

[OpenMontage chính](https://github.com/calesthio/OpenMontage) trình bày YAML stage manifests + Markdown skills và tooling ideas, nhưng dùng coding assistant làm orchestrator và AGPL-3.0; chỉ học nguyên tắc chọn lọc, không copy/import AGPL code/skills trước license assessment riêng. [Postiz](https://github.com/gitroomhq/postiz-app) phân SaaS/self-host, API và provider approvals: adapter process riêng, draft-only proof trước, rights/credentials không đi qua public CI. [Ruflo](https://github.com/ruvnet/ruflo) tham khảo ý tưởng coordination khi lợi ích multi-agent **đo được**; không tự bootstrap swarm/hooks/daemon. Đây là recommendations thiết kế từ nguồn, chưa là compatibility/performance/live proof trong AI Commerce. Chi tiết tradeoff ở [ADR B001](../architecture/ADR_B001_BRAIN_LED_INDEPENDENT_RUNTIME.md).

| Mốc có điều kiện, không cam kết ngày hoàn thành | Kết quả reviewable | Điều kiện dừng |
|---|---|---|
| Ngày 1–30 | Review B0.1, B0-S threat boundary, B0.2 journal/recovery và B1 capability plan | Source drift, insufficient mapping, custody/authority UNKNOWN giữ HOLD cho effect liên quan |
| Ngày 31–60 | B0.3 offline independent journey; tùy quyền, 1 provider proof và assisted draft/measurement | Không đổi live proof thành fixture PASS để theo lịch; không tự dispatch B1/B2 |
| Ngày 61–90 | B2 measurements → B3 next Brain brief; B4 gap memo nếu Human chọn | Không data thì NO_DATA, không economic proof thì no commercial outcome claim |

Nếu nhóm chỉ có một engineer 6h/ngày, tổng effort phase không được coi là lịch song song miễn phí. Provider access/Brain attention/private boundary có thể là critical lead time. Human có thể đổi thứ tự hoặc dừng; không tự diễn giải hết 90 ngày thành quyền phát hành/chi tiền.

## 12. B-QUALITY-001 — CIMQ + CRI R1–R8: Human-adopted PA2 planning extension (2026-11-05)

**Decision:** [Human Issue #373](https://github.com/trung-via/ai-commerce/issues/373) chọn bổ sung **Creative Intelligence, Realism & Media Quality (CIMQ + CRI)** vào PA2/PULSE hiện tại. Đây là **kiến trúc và kế hoạch được Human chọn**, không phải chứng cứ BQ-01..06 đã chạy, không phải Roadmap C và không ủy quyền media provider, publish, spend hoặc private credentials. EF01/EF02 tiếp tục là software Engineering, không biến thành media job scheduler. Brain vẫn là đúng ChatGPT chat hiện tại; PULSE vẫn là một runtime độc lập với năm semantic owner của A-series là nguồn truth.

**Tài liệu versioned:**
- [Q1–Q6 và Q3 CRI R1–R8, K1–K6 tổng hợp](../research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md)
- [ADR B002 — ranh giới Brain, PULSE, Q1–Q6, quyền và sidecar](../architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md)
- [Work Tree B-QUALITY-001 v1.0 (MD)](WORK_TREE_B_QUALITY_001_V1_0.md) / [bản JSON tương ứng](WORK_TREE_B_QUALITY_001_V1_0.json)

**Giữ nguyên lịch sử:** [Work Tree v1.1](WORK_TREE_DETAILED_V1_1.md) và [63 WBS ID gốc](WORK_TREE_V1_1.json) không sửa và không bị đánh số lại. Các BQ ID dưới đây là **bảy planning package riêng**, không tự trở thành công việc đã implement.

| BQ ID | Nội dung bổ sung có điều kiện | Priority | Trạng thái |
|---|---|---|---|
| **BQ-00** | Owner/consumer, Q1–Q6, R1–R8, K1–K6, sidecar/ref/digest migration decision | P0 | AUTHORIZED_PLANNING_ONLY |
| **BQ-01** | Q1 brief + Q3 CRI causal shot/human performance/Vietnamese audio, capability compiler mock | P0 | IMPLEMENTATION_NOT_STARTED |
| **BQ-02** | Q2 preflight adversarial critic + Q4 actual-asset timestamped multimodal inspection | P0 | IMPLEMENTATION_NOT_STARTED |
| **BQ-03** | R6 blind/pairwise Human perceptual calibration, bias/disagreement | P1 | IMPLEMENTATION_NOT_STARTED |
| **BQ-04** | Q5/R7 scoped media repair + preservation + Q4 reinspection | P1 | IMPLEMENTATION_NOT_STARTED |
| **BQ-05** | PULSE Studio media integration; only after B0-S independent trust and separate provider/rights/effect grants | P1 | IMPLEMENTATION_NOT_STARTED |
| **BQ-06** | Q6 evidence-linked behavioral learning → next Brain brief proposal using B2 source/window/asset metrics | P1 | IMPLEMENTATION_NOT_STARTED |

**Mapping PA2:** B0 = offline closed contracts/source refs; B0-S = private trust gate trước mọi external effect; B1.1 = Q1/Q3 briefs và realism; B1.2 = ModelCapabilityProfile + model-neutral compiler mock; B1.3 = Vietnamese voice/acoustics; B1.4 = Product Intelligence/rights khi có sản phẩm (product_ref=null vẫn hợp lệ); B1.5 = Q2/Q4/Q5 critic/inspector/repair, R6 human calibration; B2 = observed media/source/window/metric refs; B3 = Q6 evidence learning và Brain review; B4 = affiliate/economics/own-brand tùy chọn, không gate content-only.

**Trust & schema:** Q2 ≠ Q4; Q3 ≠ Q5; Q1 ≠ Q6. Các schema đóng CreativeDirective.v1, BrainDecision.v1, ProductJourneyReceipt.v1 giữ nguyên. Sidecar ref/digest hoặc schema v2 chỉ là phương án phải có thiết kế compatibility/migration và independent receiver verification; không được thêm quyền qua metadata chưa được chứng thực. Fiction, meme, animation, silence, content-only đều là sáng tạo hợp lệ. Không suy từ quality score thành virality, ROI, Human publish approval hay provider rights.

**Triển khai theo thứ tự, chưa dispatch:** BQ-00 (planning) → BQ-01/02 offline tests → BQ-03 tùy nhu cầu calibration → BQ-04 repair → BQ-05 chỉ sau B0-S grants + actual receipt; BQ-06 dùng B2 evidence mới có thể kết luận có cơ sở. BQ source implementation cần WorkItem, positive/negative AC, review source/consumer và PR CI Windows riêng. Không có BQ runtime, media benchmark, thương mại hoặc external outcome nào được chứng minh chỉ bởi cập nhật Roadmap này.

## 13. Dated source evidence — 2026-11-07

This appended section preserves the historical planning table and all 63 original WBS IDs.

- **BQ-04b:** PR #627 MERGED at exact main SHA `c868b989382763b03f5040390a8adaf99c25e064`. Its offline exact-fixture repair evidence does not prove PULSE provider effects or authorize edits on arbitrary A06 videos.
- **BQ-05a R2:** SOURCE REPAIR / ACCEPTANCE HOLD, base `22b4220936cc9eaf1d952ed1e5e2909d20d4e374`, action `BQ05A-R2-CODEX-XHIGH-HOSTED-CI-FAILCLOSED-TRIAGE-01`, attempt `bq05a_r2_codex_sol_xhigh_hosted_positive_hold_main_22b422_20261107_v1`. Finite internal phase triage and native identity-checked owned scratch correction; all five positives pass locally, but registered B0.3 timeouts, native link construction denials and an unrerun added diagnostic assertion keep verification incomplete. Historical hosted cause remains unproven pending exact-head hosted evidence. R1 candidate `61104898bfc3dca58f6e3c186685cad7e85955f4` / PR #632 stays Brain HOLD after hosted 1150 PASS / 5 FAIL. See [offline protocol](../creative/BQ_05A_PULSE_STUDIO_OFFLINE_QA_PROTOCOL.md). EF02 publication, new hosted CI and independent Brain acceptance remain separate and unclaimed.
- **BQ-05 LIVE:** independent private rights/per-effect permission, provider entitlement, custody, spending and attended actual-provider proof remain **NOT_STARTED / NOT_AUTHORIZED**. SOURCE-only local technical evidence is not authenticated provider or Brain approval; no provider dispatch, publish, spend or human study is authorized. Separate BQ-05b planning and Human authorization are still required.

## 14. Dated source continuation — 2026-11-08

This append preserves all historical status above, the five semantic A-series owners and all 63 original WBS IDs.

- **Accepted source baseline:** PR #639 merged at exact main `e33a3162aae34e8ffc3b57c85bc1eeef64c9880e`, following independent Brain PASS. BQ-05a acceptance is native offline technical source evidence; private authority, business quality, provider media and publication remain unproven.
- **BQ-05b.0 SOURCE readiness binder:** action `BQ05B0-CODEX-XHIGH-PULSE-LIVE-READINESS-BINDER-01`, attempt `bq05b0_codex_sol_xhigh_source_live_readiness_main_e33a316_20261108_v1`. Adds inert `BQ05bEffectPreflight.v1`: fresh native BQ-05a sidecar plus independent current replay, closed redacted technical handoff, existing B0-S2/S3a/S3b live-denial matrix and static private evidence checklist. `OFFLINE_QA_READY_FOR_BRAIN` is technical review readiness only; every external action remains `LIVE_EFFECT_HOLD`. Source implementation and executor verification observations are tracked in [WorkItem](../../work_items/BQ-05B0_EFFECT_PREFLIGHT_001.json); independent EF01/EF02, hosted Windows CI and current-chat Brain acceptance remain pending. See [protocol](../creative/BQ_05B0_EFFECT_PREFLIGHT_PROTOCOL.md).
- **BQ-05b LIVE:** **NOT_STARTED / NOT_AUTHORIZED**. Next Human selection of independent private broker/provider/account/model/region and explicit effect scope/cap/currency, followed by independently authenticated issuer/isolation/challenge/rights/entitlement/revocation/replay/budget proofs and per-effect grant, is still required. No provider dispatch, sign-in, spend, upload or publication is authorized by this source continuation. A GitHub Issue or CI green is never a Human live grant or PULSE provider receipt.


- **BQ-05b.0 executor-local verification (2026-11-08):** 1406 passed / 26 failed / 4 skipped across 1436 collected. All three new positive ACs passed; new owner 37 passed / 2 mandatory link-construction denials. Existing native consumer failures are recorded with exact test names and finite codes in the WorkItem. Source/test bytes stayed unchanged during execution. **HOLD** for independent EF01/EF02 verification or bounded separate owner/environment investigation; no candidate, hosted acceptance, current-chat Brain PASS or live provider proof claimed. One executor verification round; no owner, deadline, worker or policy relaxation.

## 15. BQ-06a source-only continuation — 2026-11-09

This append preserves the historical entries and all 63 Work Tree WBS IDs.

- **BQ-06a/Q6 R4:** fresh source repair at exact main `6777b13f9effbf242b2ebd963ee5f7580c485651`, action `BQ06A-Q6-NATIVE-SYMLINK-AGY38FLASHHIGH-R4-001`, attempt `bq06a_q6_native_symlink_6777b13f_agy38_flash_high_r4_20261109`, executor antigravity, model gemini-3.8-flash, effort high. Adds one bounded read-only A11 lineage adapter and `BQ06aLearningBriefReview.v1`, with genuine existing A17 pending-proposal review and optional current native B0.4/A06/Q4 evidence kept `UNBOUND / NOT_COMPARABLE`. Missing evidence produces `INSUFFICIENT_EVIDENCE_HOLD`. See [protocol](../creative/BQ_06A_OFFLINE_LEARNING_PROTOCOL.md) and [WorkItem](../../work_items/BQ-06A_OFFLINE_LEARNING_001.json).
- **Repair boundary:** Issue #682 R3 Codex returned QUOTA_EXHAUSTED with zero changed files; Issue #679 R2 established valid source and 15-group MT01 closure where only test environment symlink creation privilege (WinError 1314) failed in negative test setup before production guard execution. R4 solves native symlink negative test setup by explicitly catching privilege missing (WinError 1314), recording `NATIVE_SYMLINK_CREATION_UNPROVEN` and skipping only this single privilege-dependent test, while preserving mandatory hardlink and junction negative tests without skip or downgrade. Candidate publication belongs to the EF02 worker; hosted Windows CI and independent Brain review remain pending.
- **Authority:** archived local hashes are identity only, not provider attestation, current A09 replay or a Brain signature. No strategy/brief/registry mutation, auto-adoption, live PULSE/provider request, spend, publish, popularity, ROI or commercial launch is authorized or inferred.

## BQ-06b — independently reauthored offline Q6 current A09 evidence (2026-11-09)

Human Issue #730 selected **direct CI-first six-path source reauthoring** of BQ-06b from GitHub `main`; neither retry nor source-copy from #694/#698, no EF02 WorkOwner or model provider. The BQ-06b new native read-only adapter `behavioral_learning_fresh_replay.py` composes A09 `AnalyticsLedger.inspect/history`, A11 `SnapshotPin/read_snapshot`, genuine A11 learning archive and BQ06a `recheck_review`, while retaining A17 pending-only opt-in. A09 lineage is dict-based append-only `ORIGINAL/CORRECTION/REPROCESS`, with bounded full pagination and transitive supersession checks. Native test source and reviewed MT01 manifest are in `work_items/mt01-components/bq-06b.json`. **This appendix states implementation candidate scope, not PASS or launch**; exact-HEAD Windows tests, independent Brain Review and trusted merge remain mandatory, as does separate PULSE live authority for any external effect. Historical #698 remains immutable `HOLD_UNKNOWN_EFFECT`.

## B3.2 — BQ-06c pending next-brief source handoff candidate (2026-11-10)

Human Issue #739 authorizes CI-first source-only **B3.2** read-only next-Brain-brief handoff, reusing merged BQ-06b/A09/A11 native freshness and existing BQ-06a/A17 E06 pending proposal with independently pinned uncertainty, counterevidence and next-brief ref. Only an offline candidate: the actual proposal remains under A17 source ownership, the Brain makes any attended decision in current chat, and neither strategy mutation nor provider/PULSE spend or publish is authorized. New owner manifest `work_items/mt01-components/b3-next-brief.json`; exactly six changed paths with append-only Roadmap; Windows CI, independent Brain review and trusted merge remain required. Historical #698 remains HOLD_UNKNOWN_EFFECT and is not replayed.

## BQ-05b.1 — independent no-secret private broker trial source candidate (2026-11-10)

Human Issue #767 authorizes a **new independent, CI-first BQ-05b.1 source reauthor** from `main@0e6e420dc10e390d2c9d3db3b0c98adfebe40cb5`. This is not reuse of Issue #646's executor diff, original attempt, WorkOwner UNKNOWN journal or provider effects. Issue #656 source-only forensic reader was independently accepted/merged via PR #761; its historical native custody remains OPEN / HOLD for #646 UNKNOWN and #698 PROVIDER_INTENT. Do not infer journal recovery, no-effect, a released fence or a new EF02 dispatch.

New source `pulse_studio_broker_handoff.py` composes BQ-05b.0 current-byte preflight and recheck with existing BQ-05a/B0.4/A06/Q4/Q5 native owners. `BQ05bBrokerTrialHandoff.v1` and `PRIVATE_BROKER_TRIAL_INTERFACE_V1` expose deterministic canonical **read-only technical binding** `LOCAL_TRIAL_INPUT_BOUND` and six field-name-only independent private-broker negative conformance vectors. No provider, account, region, currency, cap, secrets, authenticated BrainDecision, external action or Human grant is selected. Every live effect stays denied, and UNKNOWN-effect idempotency remains fenced. See [protocol](../creative/BQ_05B1_PRIVATE_BROKER_TRIAL_HANDOFF.md), [WorkItem](../../work_items/BQ-05B1_PRIVATE_BROKER_TRIAL_HANDOFF_001.json), MT-01 owner `bq-05b1`, and Issue #767.

**Candidate only, not PASS or launch:** exact-head hosted Windows MT-01 owner+consumer/JUnit, actual B0.4 native video positive and adversarial negatives, independent Brain review and trusted merge must be checked from their own future receipts. BQ-05 LIVE remains NOT_AUTHORIZED; Human/provider private custody, grant, costs, upload/publish and commercial outcomes are not proven by source code.


## B1.3-a — current PCM WAV native observation (source-only candidate, 2026-11-10)

Human continuation of Production/BQ after BQ-05b.1 source merge selects the previously planned [Issue #716](https://github.com/trung-via/ai-commerce/issues/716) **offline** voice/acoustic measurement slice. Starting `main@feaee38ae210df539ae1c87876c58507f5596cbb`; no EF02 model provider or PULSE external effect is dispatched by this candidate.

`src/ai_commerce/creative_quality/audio_observation.py` adds the bounded read-only native PCM16/WAV observation `B13PcmAudioObservation.v1` over explicitly pinned local file bytes, separate from BQ-01b Vietnamese voice/acoustic subjective intent. Its parser reports actual sample rate, channel count, rational duration, peak, clipped samples and silence, and retains explicit `NOT_MEASURED` for pronunciation, language, dialogue, subtitle timing, voice identity, emotional quality, rights/consent, license and provider effects. A mute or silent asset may be valid. Corruption/staleness/unsafe alias/codec mismatch yields typed HOLD. SHA identity and a local reread do not prove independent custody or live permissions.

Source WorkItem `work_items/B1_3_AUDIO_OBSERVATION_001.json`, MT-01 owner `b1-3-audio`, negative tests `tests/test_b1_3_audio_observation.py`, and [protocol](../creative/B1_3_AUDIO_OBSERVATION_PROTOCOL.md). **Candidate only** until exact-head Windows MT-01, genuine artifact, independent Brain PASS and trusted merge; no changing BQ-01b/A06/A13/Q4 source owners. Human/provider/voice rights and consent are separate future gates. Original #646 WorkOwner UNKNOWN and #656 native reconciliation HOLD persist unchanged.
