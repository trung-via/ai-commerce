# AI Commerce — Kế hoạch cải tiến tích hợp 2026-11-23

**Quyết định:** Mr. Trung yêu cầu ghi vào roadmap và thực hiện theo tài liệu `AI-Commerce-ke-hoach-2026-11-22.md`. Tài liệu này là **execution overlay bổ sung** cho Roadmap A/B và Execution Register v2.2, không ghi đè lịch sử 63 WBS hoặc 7 BQ. Nguồn xác minh lúc lập: `main@388d445d2933e28af3a8a4583ab3e626f5bbfcea` (2026-11-22 UTC). Khi nguồn đổi, đối soát HEAD/base/CI/receipt trước từng tác động.

**Trạng thái:** `HUMAN_AUTHORIZED_ROADMAP / SOURCE_DELIVERY_NOT_ATTESTED`. Mục tiêu là `AUTONOMOUS_INTERNAL` theo [#993](https://github.com/trung-via/ai-commerce/issues/993) và [#994](https://github.com/trung-via/ai-commerce/issues/994), nhưng quyền account, provider/spend, media rights, publish, Windows-native WRITE và WakeHub exact-chat Send vẫn cần chứng thực riêng. **Không chuyển** một kế hoạch, PR, fixture hoặc Windows CI xanh thành chứng cứ host-native/media/commercial.

## Trình tự ưu tiên và điều kiện mở khóa

| Đợt | Mốc ước lượng | Công việc, có thể song song | Gate bàn giao đo được |
| --- | --- | --- | --- |
| W1 — thiết lập (1–2 ngày làm việc) | Bắt đầu ngay | `GOV-01` ma trận ruleset/check cách ly; `PULSE-01` hợp đồng A04→PULSE, bộ asset nguồn gốc rõ và MT-01 onboarding; đối chiếu 20 PR đang mở | Ruleset thử không gây kẹt PR hợp lệ; contract và ca thử dương/âm được đăng ký |
| W2 — khép offline (3–5 ngày tiếp) | Sau W1 | `GOV-02` đưa bảo vệ PR/CI có rollback vào main nếu đạt; `PULSE-02` outbox→receiver→journal→QA→WAIT_BRAIN; `MET-01` định danh số đo và sửa CLI | Offline journey có MP4+SHA, Q4/QA+SHA, ProductJourneyReceipt và WAIT_BRAIN đọc lại qua restart; merge-gate dùng check Brain chính xác |
| W3 — chứng thực model/Brain/Wake (tuần 2–3) | Sau các gate liên quan, nhánh độc lập | `BRAIN-01` kết nối BrainDecision.v1; `AI-01` một lần inference A10 hoặc A03 với receipt; `WAKE-01` candidate→source review→native exact Chat A | Quyết định đúng được PULSE nhận, model task+session/provenance được xác minh, WakeHub chỉ PASS với user-turn witness thật |
| W4 — một vòng quan sát (sau offline) | Khi quyền bên ngoài sẵn sàng | `DIST-01` một asset/một tài khoản/một bề mặt/một cửa sổ, A09→A11 brief mới; affiliate tùy chọn | Ref bài đăng thật, metrics đúng cohort, brief mới truy ngược asset; UNKNOWN khi thiếu |

Mốc chỉ là ước lượng; không tạo human review gate cho các bước nội bộ. Giữ các nhánh không phụ thuộc chạy song song; không buộc WakeHub để hoàn thành PULSE offline. Tránh chạy lại test đã có bằng chứng exact-head/consumer hợp lệ theo merge gate hiện hành.

## Work packages và nghiệm thu

### GOV-01/02 — PR, Windows CI và independent Brain check

- **Owner:** repository governance + Engineering CI; **consumer:** GitHub trusted merge workflow.
- Chụp ruleset đang hiệu lực, xác định tên/source của required Windows check và check `Brain-before-merge`; lập ca thử trên nhánh/ruleset cách ly: green, red, missing, stale HEAD, base drift chạm/không chạm consumer, original-proof EF02 và CI Windows mới. Không bật bắt buộc trên `main` trước khi chứng minh PR hợp lệ vẫn merge bằng đúng workflow identity.
- Tách Brain gate check khỏi job vừa ghi trạng thái vừa merge; trạng thái HOLD/missing/revoked phải **không** thành required PASS dù script merge-gate kết thúc exit 0. Kiểm stale base và nguồn phát check. Sau merge #1321, đối soát event `GITHUB_TOKEN` bị bỏ qua và thêm bounded re-evaluate theo merge SHA nếu cần.
- **Positive AC:** effective ruleset đọc lại được; CI xanh + Brain đúng HEAD/base merge thành SHA; original-proof hợp lệ được giữ. **Negative AC:** red/missing/stale/forged HOLD bị chặn, không gây vòng test lại vô ích. **Rollback:** phục hồi chính ruleset mới áp nếu khóa PR hợp lệ.
- **Authority:** kiểm tra ruleset ở chế độ read-only và chuẩn bị draft được làm ngay; kích hoạt repo admin ruleset sau ca thử và chứng thực quyền hiệu lực, không suy diễn đã áp.

### PULSE-01/02 — A04 → PULSE → Q4/QA offline

- **Owner:** A04 Media & Creative, ProductionPipelineOrchestrator; **consumer:** PULSE B0/B1, Quality Q4; **input:** `A04CreativeHandoffReceipt.v1` + `CreativeDirective.v1` + asset ref/SHA, source SHA, journey/cycle, consent for offline use; **output:** PULSE journal + MP4 ref/SHA + QA ref/SHA + `ProductJourneyReceipt.v1` ở `WAIT_BRAIN`.
- Chọn một brief `product_ref=null`, A03 package đã lưu và asset local có nguồn/quyền kiểm thử. Giữ metadata handoff trong orchestrator, **không đổi bytes** schema PULSE v1 đang đóng. Đăng ký consumer route và MT-01 scope trước khi tạo EF02 implementation PR.
- A04 ghi immutable artifact và `ProductionStageCompleted.v1` trong SQLite outbox; router xác nhận digest/schema/rights và phát `ProductionStageDispatched.v1`; PULSE đọc lại byte, kiểm SHA, dedup `(journey,cycle,stage,consumer,digest)` và trả durable `ProductionStageReceipt.v1`. Chỉ ACK khi mở lại được journal/receipt của receiver. Reuse local media import/render và Q4/B1.5, không tự gọi provider bên ngoài.
- **Positive AC:** một trigger→một PULSE effect; MP4 giải mã được và Q4 hash đúng; `WAIT_BRAIN` survives restart; A10→A03/A03→A04 không suy giảm. **Negative AC:** trùng outbox, byte thay, sai quyền/schema/phiên bản, crash trước sau dispatch, UNKNOWN_EFFECT chưa được reconcile đều không chạy lại tác động mù quáng.
- **Source bounds ban đầu:** `src/ai_commerce/media/generation.py`, `src/ai_commerce/production_orchestration/{contracts.py,orchestrator.py,...}`, receiver trong `src/ai_commerce/production_runtime/`, owner/consumer tests; actual allowed paths chốt tại WorkItem sau khi xác minh registration.

### BRAIN-01 — nhận BrainDecision.v1 độc lập

- **Owner:** PULSE Bridge và Brain reviewer đáng tin cậy; **consumer:** PULSE state machine.
- Từ `WAIT_BRAIN`, chuyển packet đã lọc kèm hashes, QA, directive/receipt identity tới một transport có provenance/authentication; nhận `BrainDecision.v1`, kiểm version/journey/digest/rights rồi cập nhật state. Đây không phải GitHub Brain PASS cho source PR.
- **Positive AC:** một quyết định hợp lệ tiếp nhận đúng một lần và đọc lại được. **Negative AC:** wrong journey, hash, signer, duplicate, missing reviewer giữ WAIT_BRAIN/HOLD; publish vẫn đòi quyền riêng. Nếu chưa có transport đáng tin thì chưa tuyên bố auto Brain.

### AI-01 — một inference thật, chứng thực theo task

- **Owner:** A10/A03 Connector, Engineering MT-01 + host execution; **consumer:** M05→A03 hoặc A03→A04.
- Ưu tiên một bounded A10 task nếu mục tiêu là chứng minh suy luận→brief; nhánh AGY A03 duy trì riêng. Ghi source SHA, task digest, thực tế session/model/effort (nếu chứng thực được), structured result, output digest và consumer receipt. `TEST_ONLY_MOCK`/structural diagnostic/`MODEL_VERIFIED` phải tách bạch.
- **Current known gate:** PR [#1324](https://github.com/trung-via/ai-commerce/pull/1324) đang mở; Windows run [45009572725](https://github.com/trung-via/ai-commerce/actions/runs/45009572725) fail tại MT-01 mapping preflight. Sửa registration/CI đúng nguồn trước native one-shot; không retry không rõ quota/session. Human chọn đường connector mở cho Codex, AGY và ChatGPT Brain qua CDP, **chưa cố định stage-default model**.

### WAKE-01 — source-to-native-to-one exact Chat A Send

- **Owner:** WakeHub G3/WH07 + đúng HostOps; **consumer:** chính Chat A được đăng ký.
- Đối chiếu [#1323](https://github.com/trung-via/ai-commerce/issues/1323) candidate HEAD/base/CI, tìm PR hiện có trước khi tạo mới. Native status cần independent signer SID, SCM owner/creation-time, nguồn/generation và đúng CDP 9444; chỉ source merge không chứng minh native.
- Chọn một authenticated EF02-completion event; Issue [#1317](https://github.com/trung-via/ai-commerce/issues/1317) là *issue-created*, chưa tự động là loại event consumer hỗ trợ. Nếu cần #1317 làm trigger, đăng ký event contract riêng. `UNKNOWN_EFFECT` cũ của #1270 reconcile bằng identity gốc, không replay.
- **Positive AC:** một user turn thực quan sát ở đúng Chat A, đúng account/event và one-shot grant. **Negative AC:** wrong chat, expired grant, duplicate/reservation ambiguous giữ HOLD/UNKNOWN. Tool-call plugin tiếp theo là mốc độc lập.

### MET-01 — benchmark A03, portfolio North Star và CLI

- **Owner:** Brain Measurement + A03 + Foundation; **consumer:** báo cáo danh mục/CLI.
- Giữ A03 weighted benchmark 55/20/15/10 với tên wire lịch sử, nhưng **label** rõ `A03 benchmark`, phân biệt portfolio `Σ(w×distinct verified outcome)/(T/T_ref+K/K_ref+H/H_ref)` phiên bản hóa. Đo queue/retry wall time, tất cả input/output tokens, và active Human effort quan sát; thiếu coverage→UNKNOWN/INCOMPLETE, không tự gán H=0.
- Sửa `about --json` để tách offline source capability và actual host/product runtime, giữ compatibility consumer cũ; mỗi dòng trạng thái nêu SHA, owner, evidence source, date.
- **Positive AC:** one actual outcome counted once, version + coverage hiện rõ. **Negative AC:** duplicate, synthetic, missing T/K/H, stale proof không làm tăng điểm. Không sửa execution register v2.2 thành snapshot hiện tại giả.

### DIST-01 — A07/A08 → A09 → A11, A16 tùy chọn

- **Owner:** Distribution, Analytics, Learning; **consumer:** next-cycle A10/A03.
- Một nội dung/asset/account/surface/window, `product_ref=null` hợp lệ. Chỉ khi account/publish được cho phép mới ghi publish receipt thật, A09 nhập raw metrics/ref đúng epoch, A11/A17 làm kết luận phản chứng và brief có ref nguồn cho vòng sau. Affiliate A14/A16 chỉ chạy khi được chọn và có chứng cứ attribution, refunds, settlement, currency/time window.
- **Positive AC:** next brief truy ngược verified article/account/window/asset/source metrics. **Negative AC:** wrong scope, missing provider observation, fake ROI hoặc synthetic business growth đều HOLD/UNKNOWN.

## Bước khởi động và sổ bằng chứng

1. `W1-A`: tạo ma trận ruleset + rollback + required-check provenance trong WorkItem governance riêng; không chỉnh live ruleset cho đến khi kiểm thử cách ly PASS.
2. `W1-B`: đăng ký A04→PULSE consumer/contract + MT-01 WorkItem; EF02 source change trên nhánh riêng, Windows CI, independent Brain review rồi mới merge.
3. `W1-C`: đối soát các PR đang mở để tránh duplicate/retest; trước mắt #1324 mapping HOLD và #1323 candidate→PR; nhánh WAKE không chặn PULSE offline.

Mỗi WorkItem ghi `status | main SHA | source/candidate SHA | owner→consumer | action/attempt | PR+CI | authentic Brain decision | receiver receipt | missing condition`. Trạng thái theo ladder `PLANNED→AUTHORIZED→INGRESS_VALIDATED→EXECUTOR_INVOKED→CANDIDATE_VERIFIED→CI_VERIFIED→BRAIN_ACCEPTED→MAIN_MERGED→NATIVE_OBSERVED→EXTERNAL_EFFECT_OBSERVED→COMMERCE_OBSERVED`; chỉ đánh dấu chặng có biên nhận thật, `UNKNOWN` không bao giờ là 0/PASS. WorkItem không tự cấp quyền provider, admin ruleset, native WRITE hay Send.

**Tham chiếu:** [Roadmap A](ROADMAP_A_CREATIVE_FIRST_V1_0.md), [Roadmap B](ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md), [Execution Register v2.2](ROADMAP_EXECUTION_REGISTER_V2_2.md), [#993](https://github.com/trung-via/ai-commerce/issues/993), [#994](https://github.com/trung-via/ai-commerce/issues/994), file đề xuất Human `AI-Commerce-ke-hoach-2026-11-22.md` (đầu vào, không phải runtime evidence).
