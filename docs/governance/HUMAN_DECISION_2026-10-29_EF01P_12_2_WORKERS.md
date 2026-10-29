# Human Decision — EF01-P pytest-xdist 12–2 host allocation

**Ngày:** 2026-10-29  
**Repo:** `trung-via/ai-commerce`  
**Human authority:** Chỉ thị trực tiếp trong cuộc hội thoại AI Commerce.  
**WorkItem:** `EF01-DIRECT-GIT-PYTEST-003` (MT-01 + Direct Git + xdist).  
**Trạng thái:** HUMAN-APPROVED REQUIREMENT / IMPLEMENTATION NOT YET VERIFIED.  
**Source lúc lập:** `main@13c24705e32820e80fe0b9b7c91713b61bee6c77`.

## Quyết định thay thế thiết kế allocation trước đây

Human đã thử thực tế các cấu hình 2, 4, 8, 12, 16 pytest workers và xác định **12 workers nhanh nhất**. Đây là **HUMAN_REPORTED_PRIOR_BENCHMARK**, không giả làm benchmark mới được CI/Brain trực tiếp thu thập; không yêu cầu lặp lại benchmark hay tạo performance approval gate.

**12 là giá trị mặc định bắt buộc ở tầng cấu hình cho EF01 pytest verification** (local EF01 và EF02→EF01), miễn là xdist và máy chủ có thể khởi chạy an toàn. Không auto-tuning giảm xuống 4/8 chỉ vì thêm action hoặc vì thấy CPU cao bình thường. Pytest worker khác với executor/model invocation.

Cấp phát theo **một Windows host vật lý**, xuyên qua các EF02 batches và không reset bộ đếm theo từng batch:

| Concurrent EF01 pytest actions | Primary/first action | Following actions | Desired worker total |
|---|---:|---:|---:|
| 1 | 12 | — | 12 |
| 2 | 12 | 2 | 14 |
| 3 | 12 | 2 + 2 | 16 |
| 4 | 12 | 2 + 2 + 2 | 18 |

Dạng tổng quát `N >= 1`: **`12 + 2 * (N - 1)`**, khi tất cả action đều đang trong giai đoạn pytest và không có ngoại lệ hạ tầng.

- Primary = action đang hoạt động có **thứ tự bắt đầu/được nhận vào EF01 sớm nhất trên host**; dùng thứ tự/lease duy nhất có chứng cứ, tie-break ổn định. Một primary lease **12** cho toàn host; các secondary lease **2**. Không dành một primary 12 cho từng batch.
- Khi primary hoàn tất, giải phóng lease; primary chuyển cho action active tiếp theo theo thứ tự khi đạt **ranh giới kiểm thử an toàn kế tiếp**, hoặc action mới bắt đầu. Không hủy/restart một pytest `-n 2` đang chạy để cố đổi giữa chừng. Khi không có action eligible đang ở điểm khởi chạy pytest, giữ suất ưu tiên 12 sẵn sàng; không tuyên bố liên tục có tiến trình 12 workers thực tế.
- Không thay đổi `EF02 max_parallel=2` mặc định. Nếu Human cấu hình 3 hoặc nhiều EF02 action thực sự chạy song song, quy tắc 12–2 vẫn áp dụng; con số 3/4 trong bảng là kịch bản, không phải thay mặc định EF02.
- Allocation áp dụng cho mọi verify invocation của EF01, gồm sau repair và fan-in; MT-01 lựa chọn **test scope**, 12–2 lựa chọn **độ song song pytest**, hai trách nhiệm tách biệt.
- Bảo đảm tính nhất quán khi có nhiều tiến trình/batch đồng thời: dùng host-wide atomic lease/lock (không phải mutex chỉ nằm trong mỗi Python process), xử lý crash/stale lease một cách fail-closed và ghi nhật ký owner/action/attempt, requested & actual workers, acquire/release, fallback/reason. Không tạo duplicate provider/action claim.
- Resource checks **chỉ mức tối thiểu**: xdist có khả dụng, pytest worker có khởi động được, không gặp thiếu tài nguyên nghiêm trọng/fixture race hoặc OS error. Không bắt benchmark lại 2/4/8/16. Thiếu xdist hoặc lỗi launch -> trả `XDIST_UNAVAILABLE` / `HOST_RESOURCE_UNSAFE` với evidence và serial-safe fallback nếu hợp lệ; **không được lặng lẽ thay 12 thành 4/8 và gọi đó là default đã đạt**. Secondary mặc định 2 khi xdist hợp lệ.
- Các trường hợp degraded/serial cần phân biệt `verification_pass_for_scope` với `12_worker_conformance_not_verified`; không bịa tốc độ, không biến test failures/timeout/pytest no-tests thành PASS.
- GitHub-hosted CI không bị buộc áp 12 trên host khác. Tính năng 12–2 là EF01 action verification policy trên Windows host; CI vẫn có thể chạy registered serial profile đúng sự thật cho đến khi runtime/profile mới được đưa vào main.

## Acceptance criteria

1. Một EF01 action đủ điều kiện: verify argv thực sự chạy với `-n 12`, logs chứng minh con số, không chỉ giá trị cấu hình.
2. Hai action đồng thời trên cùng host: đúng `12 + 2`, dù khác EF02 batches; không có `12 + 12`.
3. Ba action khi `max_parallel>=3`: đúng `12 + 2 + 2`, không thêm suất 12; default EF02 vẫn là 2.
4. Primary hoàn tất: chuyển quyền 12 một cách deterministic tại ranh giới verify; không kill/rerun verification đang chạy chỉ để đổi slot.
5. Nếu xdist/host bị lỗi: bằng chứng có reason và fallback/stop thật; không gắn `WORKERS_12_VERIFIED`.
6. Repair/fan-in không làm mất AC coverage hoặc reuse sai PASS, không làm tăng invocation model ngoài chính sách.
7. Multi-process/batch races/crash có kiểm thử deterministic với evidence; không thay source/claim/dedup identity.
8. Không yêu cầu benchmark mới, không thêm pilot/shadow/approval gate.

## Điều phối với Issue #81

Issue `#81` được submit trước khi Human làm rõ phân bổ; batch đã submit không được sửa một cách giả định worker chưa đọc. Chỉ thị này **supersedes** các câu chung chung về host budget / auto-select 4/8/12 trong instruction cũ. Nếu worker đã nhận snapshot cũ, cần kiểm tra candidate và bổ sung implementation patch hợp lệ trước khi merge, **không replay** attempt cũ hoặc giả claim rằng comment sau ingress được worker tự nhận.

Không auto merge main, không tự tuyên bố Windows 12–2 đã chạy; nghiệm thu dựa trên code + test + runner artifacts thực tế.
