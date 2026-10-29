# Human Decision — MT-01 Minimum Test in EF01/EF02

**Ngày:** 2026-10-29  
**Dự án:** AI Commerce (độc lập AIOS-Renew)  
**Trạng thái:** HUMAN-ADOPTED; SCOPED LOCAL IMPLEMENTATION VERIFIED; EF02 REGISTRY ACTIVATION PENDING
**Nguồn lúc cập nhật:** `trung-via/ai-commerce@a8489e9974b3e89e5c7eeb9bf36de9693290d29e`

## Quyết định

Human đồng ý bổ sung **MT-01 — Minimum Test** vào nền tảng Engineering Flow. Dùng **WorkItem EF01-P đã có** (`EF01-DIRECT-GIT-PYTEST-003`), kết nối vào EF01-R Verify–Repair và EF02 per-action verification. **Không lập WBS mới**, không tăng số 63 WBS cơ sở, không mở lại M0-R đã nghiệm thu có deferred risks, không tạo gate cho A03/A04/A05/A06 hoặc Creative Factory. Đây là quyết định chuẩn hóa **phạm vi triển khai**, không phải bằng chứng code/run PASS.

## Hành vi mục tiêu của MT-01

1. **Minimal sufficient selection:** Với mỗi action, xác định tập test tối thiểu chứng minh các AC liên quan dựa trên changed paths, call sites/consumers, contract, test registry và dependency ảnh hưởng. Ưu tiên test tái hiện lỗi, unit/contract bị ảnh hưởng, integration consumer; chỉ mở rộng regression theo ảnh hưởng thực. Không chọn rỗng/loại bỏ AC chỉ để được PASS.
2. **Execution vs reuse:** Từng test/nhóm test được ghi `EXECUTE`, `REUSE`, `INVALIDATED` hoặc `UNPROVEN`. REUSE chỉ được phép khi bằng chứng PASS có đủ fingerprint: commit/tree hoặc hashes của file liên quan, test definition/argv, dependency versions, execution environment, coverage scope và AC mapping; mọi thay đổi ảnh hưởng làm invalidated. Không mặc định xem PASS lịch sử trên source khác là hợp lệ.
3. **Verify–Repair:** EF01-R chỉ gửi lỗi pytest xác định, stdout/stderr và diff cho executor/model/effort đã được chọn. Sau sửa, chạy lại phần test cần thiết theo dependency; test PASS cũ chỉ được reuse khi không bị tác động. Giữ giới hạn vòng sửa, cùng action/claim, và điều kiện dừng đối với timeout/UNKNOWN/quota/permission/source drift. Không thay thế bằng full-suite lặp không có lý do.
4. **EF02 integration:** Mỗi action có verification profile đã đăng ký; chọn tập test qua cơ chế deterministic/approved, không tự mở shell/argv tuỳ ý. Preserve source SHA, attempt fingerprint, worktree và evidence. Fan-in của nhiều candidate phải invalidated evidence bị ảnh hưởng và chạy consumer/integration test của kết quả kết hợp; PASS riêng rẽ của sibling không tự bằng PASS aggregate.
5. **Fallback đúng sự thật:** Nếu thiếu dependency map/AC evidence hoặc fingerprint không đủ chắc, chạy profile rộng hơn phù hợp hoặc trả `UNPROVEN`/FAIL, không gắn nhãn VERIFIED. Infra/pytest no-tests không phải PASS.
6. **Quyết định Human cuối cùng: 12 workers cho MỖI action.** Local EF01, EF02→EF01, EF01-R reverify và fan-in đủ điều kiện dùng `pytest-xdist -n 12` riêng; hai action đồng thời dùng 12+12=24 workers. Giữ mặc định EF02 `max_parallel=2`. Không primary/secondary, lease 12 toàn host, chia CPU/RAM theo action hay tự hạ 2/4/8 khi action khác đang chạy. Human đã thử 2/4/8/12/16, chọn 12 nhanh nhất và xác nhận host đủ 24: **HUMAN_REPORTED**, không tạo benchmark/capacity gate mới. Chỉ thiếu xdist hoặc lỗi launch/resource/fixture thực tế được ghi nhận mới cho fallback serial hoặc STOP. Không suy ra 12/24 live từ cấu hình. Direct Git vẫn là executable tùy chọn, argv không shell.

## Điều kiện nghiệm thu MT-01 (áp dụng khi triển khai)

- Patch chỉ chạm một component: chỉ chạy test tối thiểu đủ AC/consumer; evidence ghi cách chọn và phần chưa kiểm chứng.
- Có PASS đủ fingerprint, không thay source/dep: REUSE đúng; thay file liên quan hoặc test logic: INVALIDATED rồi EXECUTE.
- EF01-R fail → fix: chạy lại failed/affected tests và giữ bằng chứng còn hiệu lực; không tạo duplicate action/provider call ngoài repair policy.
- Dependency fan-in từ 2 candidates: invalidated khi tích hợp; integration test của aggregate chạy thật hoặc báo UNPROVEN.
- Không có test phù hợp, thiếu evidence, timeout/UNKNOWN, pytest internal/no-tests hoặc nonzero: không báo BUILD_VERIFIED_FOR_SCOPE giả.
- Mỗi eligible pytest verify mặc định `-n 12`; hai action mặc định 12 mỗi action, giữ `max_parallel=2`. Ghi argv, requested/actual workers, exits và overlap; serial PASS không chứng minh 12/24 live.

## Phạm vi kỹ thuật gợi ý

`src/ai_commerce/engineering/runner.py`, `src/ai_commerce/remote/worker.py`, `src/ai_commerce/remote/validation.py`, `.github/ef02/verify-profiles.json`, `pyproject.toml`, tests tương ứng và nếu cần một helper nhỏ thuần Python cho selection/evidence. Bảo toàn tương thích RemoteActionBatch v1; chỉ mở rộng schema có kiểm thử khi thực sự cần, không thêm runtime riêng hoặc lifecycle AIOS.

**Trạng thái lúc quyết định:** Planning/canonicalization only. Chưa thực thi executor, chưa chạy test MT-01, chưa kích hoạt profile, chưa thay Windows runner service, chưa dispatch EF02, chưa tự merge candidate code.

## Bounded recovery và quyết định thay thế — 2026-10-29

[PR #82](https://github.com/trung-via/ai-commerce/pull/82), head `a598a9b165f557bbaf797cda1e90745666c9d1c9`, xác nhận quyết định cuối **12 mỗi action**, thay toàn bộ đề xuất 12+2 trước đó. Không merge/copy branch đó ngầm. WorkItem hiện có được cập nhật trong phạm vi recovery.

Parent Issue #81 / run `40840406005`, attempt `ef01p-mt01-codex61sol-max-20261029-01`, kết thúc **SCOPE_VIOLATION / CHANGES_OUTSIDE_ALLOWED_SCOPE**, verify exit NULL, không candidate. Local logs cũ không đổi verdict đó thành PASS. Recovery chỉ đọc worktree cũ, kiểm base/hash/allowlist, rồi kiểm chứng candidate mới; xem `docs/engineering/EF01_P_MT01_IMPLEMENTATION_REPORT.md` cho kết quả thực tế.

Registered-only vẫn bắt buộc. `.github/ef02/verify-profiles.json` ở base hiện chỉ có serial argv. Recovery không sửa protected registry và không tự chèn flags vào authoritative argv. Runtime hỗ trợ đăng ký chính xác suffix `-n 12`; kích hoạt EF02 song song/narrow selector cần thay registry qua host/PR riêng. Serial registration được ghi là **registration gap**, không giả làm resource fallback hoặc proof 12 workers.

Local recovery verification: `999 passed, 3 skipped in 767.97s (0:12:47)`, exit 0; supplemental Windows proof observed two simultaneous twelve-worker actions. These are local receipts; authoritative EF02 verification/publication and protected registry activation remain separate. See the implementation report for hashes, observed failures and remaining gaps.
