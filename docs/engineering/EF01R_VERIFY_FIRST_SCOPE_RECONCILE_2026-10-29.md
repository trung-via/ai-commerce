# EF01-R — Verify-First, Scope-Reconcile

**Publication policy superseded 2026-10-30:** the historical no-candidate rule for ordinary verified drift below is replaced by [Verify First, Publish Candidate, Brain Scope Review](../governance/HUMAN_DECISION_2026-10-30_CANDIDATE_SCOPE_REVIEW.md). EF01 keeps its distinct verification receipt; EF02 publishes only with intact proof and explicit pending Brain review.

**Ngày:** 2026-10-29  
**Human source:** Yêu cầu trực tiếp trong hội thoại AI Commerce: tận dụng năng lực executor và EF01-R, không hard-stop trước pytest chỉ vì thay đổi file nằm ngoài allowlist.  
**Project:** AI Commerce độc lập AIOS-Renew.  
**Trạng thái:** Candidate code + regression tests được đề xuất qua PR riêng; chưa tuyên bố Windows/EF02 PASS hoặc merge main.

**Human approval bổ sung (2026-10-29):** Component-Level Scope + Verify-First là chiến lược authoring mặc định theo component/AC/consumer. Xem [Human Decision — Component Scope + Verify-First](../governance/HUMAN_DECISION_2026-10-29_COMPONENT_SCOPE_VERIFY_FIRST.md). Brain tiếp tục chủ động chọn `max_parallel` phù hợp từng batch, không ép cố định 2 cho mọi request; MT-01 và EF01-P giữ nguyên quyết định riêng.

## Vấn đề được quan sát

- Issue #81 / workflow 40840406005 chạy executor nhưng kết thúc `SCOPE_VIOLATION`, không chạy verification; 5/19 file ngoài allowlist.
- Issue #84 đã mở rộng allowlist để giải quyết, nhưng một lượt mới có thể phải re-invoke mô hình và tốn thêm thời gian.
- Root cause: `src/ai_commerce/engineering/runner.py` đã gọi `_source_problem` kiểm tra `scope_valid` **trước** pytest.

## Mục tiêu quyết định

**Tách verify evidence khỏi scope/publication authorization.** Đây không phải "bỏ scope". Cho executor quyền mở rộng giải pháp bên trong disposable worktree để chứng minh AC, nhưng candidate phải còn được đối chiếu phạm vi trước khi xuất bản.

### Flow

1. Executor thay đổi source trong worktree riêng → chụp HEAD, Git identity, changed paths, diff, SHA và scope delta.
2. Trước pytest, vẫn dừng với: HEAD/worktree drift, protected/control/secret path changed, permission denied, provider timeout/quota/unknown hoặc tool outcome ambiguous. Không replay model.
3. Với **ordinary out-of-scope code files**, không dừng sớm. Chạy **registered verification profile** (MT-01 khi được triển khai, hoặc profile hiện có) và lưu exit, stdout/stderr, test coverage cùng changed-path evidence.
4. Nếu pytest phát hiện lỗi xác định → EF01-R có thể tiếp tục bounded repair với đúng executor/model/effort, cùng claim/worktree. Repair có thể thu gọn scope hoặc sửa AC failure; không ép xoá những file thực sự cần thiết chỉ để khỏi báo scope drift.
5. Nếu pytest PASS và scope hợp lệ → `BUILD_VERIFIED_FOR_SCOPE` như hiện tại; EF02 publisher vẫn kiểm tra source/path/hashes trước candidate.
6. Nếu pytest PASS nhưng còn ordinary out-of-scope files → `VERIFIED_SCOPE_REVIEW_REQUIRED`; attach `out_of_scope_files`, `verify_exit_code=0`, test artifacts/diff. **Không** đồng nhất với `BUILD_VERIFIED_FOR_SCOPE`, **không** publish candidate hoặc merge main. Brain có đủ bằng chứng để đánh giá mở rộng scope trong **lượt được ủy quyền mới** / reuse verified patch dựa trên hash; tránh tạo lại toàn bộ code theo mặc định.
7. Nếu pytest FAIL và không thể sửa trong ngân sách → giữ đúng `VERIFICATION_FAILED` và log cả scope delta; không gộp test failure thành scope failure.

### Phạm vi bổ sung được xử lý thế nào

- Đường dẫn mã nguồn/test thuộc component đang làm, có giải thích dependency: đề xuất mở rộng scope dựa trên diff + AC/consumer; dùng prior verified artifacts làm nguồn phục hồi khi hash/source/environment hợp lệ.
- Đường dẫn không liên quan: yêu cầu executor thu gọn trong repair hoặc phân loại riêng, không tự đưa vào publish.
- Đường dẫn `.github/`, `.git/`, `.local/`, `.env*`, secrets/credentials là **protected** khi thay đổi ngoài đường vận hành; không chạy mã thử nghiệm sau khi phát hiện. Không dùng thay đổi scope để vượt sandbox/credential/remote action authority.
- Test tự biến đổi source trong quá trình verify → `SOURCE_DRIFT`/scope error, không coi là thành công.
- Trên EF02, allowed_paths thuộc request fingerprint bất biến; comment Issue/Brain proposal không thay đổi JSON mà workflow đã snapshot. Việc phê duyệt phạm vi bổ sung dùng một attempt/ref hợp lệ mới, không sửa âm thầm claim cũ.
- Có thể dùng allowed_paths rộng theo component thay vì từng file ngay từ đầu, nhưng không bỏ phân tách protected paths hoặc quyền GitHub.

## Acceptance và regression

- Ordinary unlisted file + pytest PASS → verified evidence, `VERIFIED_SCOPE_REVIEW_REQUIRED`, không candidate.
- Ordinary unlisted file + pytest FAIL → bounded repair được phép; khi scope được xử lý và test PASS → `BUILD_VERIFIED_FOR_SCOPE`.
- Ordinary unlisted file + pytest FAIL và không giải quyết → `VERIFICATION_FAILED`, không candidate.
- Protected path change → dừng trước pytest và trước bất kỳ repair bất định.
- Provider unknown/permission/timeout hoặc unexpected git HEAD/worktree → giữ stop semantics.
- Verifier mutated code/worktree → fail, không nâng thành PASS.
- Preserve default EF02 `max_parallel=2`; mỗi action dự kiến `-n12` theo quyết định Human riêng tại PR #82, nhưng tính năng mới này không kích hoạt xdist.
- CI/target tests và live Windows runner evidence phải được quan sát thực trước khi tuyên bố triển khai.

## Triển khai phạm vi nhỏ, không tạo lifecycle mới

Bản vá tối thiểu:
- `src/ai_commerce/engineering/runner.py`: verify-first mode mặc định, strict opt-out, evidence `out_of_scope_files`, terminal status rõ ràng; giữ publisher gate.
- `tests/test_ef01_repair.py`, `tests/test_ef01.py`: kiểm tra scope PASS/FAIL, same action repair, protected path, source mutation/legacy strict behavior.

**Không auto-merge main, không auto-grant GitHub Issue permissions, không thay đổi EF02 RemoteActionBatch v1 schema**, không biến testing thành quyền mở rộng scope tự động.
