# Human Decision — MT-01 Minimum Test in EF01/EF02

**Ngày:** 2026-10-29  
**Dự án:** AI Commerce (độc lập AIOS-Renew)  
**Trạng thái:** HUMAN-ADOPTED FOR PROJECT ROADMAP; IMPLEMENTATION NOT YET VERIFIED  
**Nguồn lúc cập nhật:** `trung-via/ai-commerce@a8489e9974b3e89e5c7eeb9bf36de9693290d29e`

## Quyết định

Human đồng ý bổ sung **MT-01 — Minimum Test** vào nền tảng Engineering Flow. Dùng **WorkItem EF01-P đã có** (`EF01-DIRECT-GIT-PYTEST-003`), kết nối vào EF01-R Verify–Repair và EF02 per-action verification. **Không lập WBS mới**, không tăng số 63 WBS cơ sở, không mở lại M0-R đã nghiệm thu có deferred risks, không tạo gate cho A03/A04/A05/A06 hoặc Creative Factory. Đây là quyết định chuẩn hóa **phạm vi triển khai**, không phải bằng chứng code/run PASS.

## Hành vi mục tiêu của MT-01

1. **Minimal sufficient selection:** Với mỗi action, xác định tập test tối thiểu chứng minh các AC liên quan dựa trên changed paths, call sites/consumers, contract, test registry và dependency ảnh hưởng. Ưu tiên test tái hiện lỗi, unit/contract bị ảnh hưởng, integration consumer; chỉ mở rộng regression theo ảnh hưởng thực. Không chọn rỗng/loại bỏ AC chỉ để được PASS.
2. **Execution vs reuse:** Từng test/nhóm test được ghi `EXECUTE`, `REUSE`, `INVALIDATED` hoặc `UNPROVEN`. REUSE chỉ được phép khi bằng chứng PASS có đủ fingerprint: commit/tree hoặc hashes của file liên quan, test definition/argv, dependency versions, execution environment, coverage scope và AC mapping; mọi thay đổi ảnh hưởng làm invalidated. Không mặc định xem PASS lịch sử trên source khác là hợp lệ.
3. **Verify–Repair:** EF01-R chỉ gửi lỗi pytest xác định, stdout/stderr và diff cho executor/model/effort đã được chọn. Sau sửa, chạy lại phần test cần thiết theo dependency; test PASS cũ chỉ được reuse khi không bị tác động. Giữ giới hạn vòng sửa, cùng action/claim, và điều kiện dừng đối với timeout/UNKNOWN/quota/permission/source drift. Không thay thế bằng full-suite lặp không có lý do.
4. **EF02 integration:** Mỗi action có verification profile đã đăng ký; chọn tập test qua cơ chế deterministic/approved, không tự mở shell/argv tuỳ ý. Preserve source SHA, attempt fingerprint, worktree và evidence. Fan-in của nhiều candidate phải invalidated evidence bị ảnh hưởng và chạy consumer/integration test của kết quả kết hợp; PASS riêng rẽ của sibling không tự bằng PASS aggregate.
5. **Fallback đúng sự thật:** Nếu thiếu dependency map/AC evidence hoặc fingerprint không đủ chắc, chạy profile rộng hơn phù hợp hoặc trả `UNPROVEN`/FAIL, không gắn nhãn VERIFIED. Infra/pytest no-tests không phải PASS.
6. **12 workers và Direct Git:** EF01-P vẫn chứa Direct Git và lựa chọn `pytest-xdist -n 12`, song song hoá *tests*, không phải 12 model calls; cần khai báo dependency khi triển khai và kiểm chứng Windows isolation/memory/host oversubscription với EF02 max_parallel=2. Serial phải còn dùng được; đo hiệu năng chỉ mang tính quyết định cấu hình, **không là gate benchmark/pilot bắt buộc**.

## Điều kiện nghiệm thu MT-01 (áp dụng khi triển khai)

- Patch chỉ chạm một component: chỉ chạy test tối thiểu đủ AC/consumer; evidence ghi cách chọn và phần chưa kiểm chứng.
- Có PASS đủ fingerprint, không thay source/dep: REUSE đúng; thay file liên quan hoặc test logic: INVALIDATED rồi EXECUTE.
- EF01-R fail → fix: chạy lại failed/affected tests và giữ bằng chứng còn hiệu lực; không tạo duplicate action/provider call ngoài repair policy.
- Dependency fan-in từ 2 candidates: invalidated khi tích hợp; integration test của aggregate chạy thật hoặc báo UNPROVEN.
- Không có test phù hợp, thiếu evidence, timeout/UNKNOWN, pytest internal/no-tests hoặc nonzero: không báo BUILD_VERIFIED_FOR_SCOPE giả.
- xdist 12 worker là profile tuỳ chọn với bằng chứng đúng trên Windows, không thay mặc định EF02 concurrency.

## Phạm vi kỹ thuật gợi ý

`src/ai_commerce/engineering/runner.py`, `src/ai_commerce/remote/worker.py`, `src/ai_commerce/remote/validation.py`, `.github/ef02/verify-profiles.json`, `pyproject.toml`, tests tương ứng và nếu cần một helper nhỏ thuần Python cho selection/evidence. Bảo toàn tương thích RemoteActionBatch v1; chỉ mở rộng schema có kiểm thử khi thực sự cần, không thêm runtime riêng hoặc lifecycle AIOS.

**Trạng thái lúc quyết định:** Planning/canonicalization only. Chưa thực thi executor, chưa chạy test MT-01, chưa kích hoạt profile, chưa thay Windows runner service, chưa dispatch EF02, chưa tự merge candidate code.
