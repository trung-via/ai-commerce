# Human Decision — EF01-P: 12 pytest workers cho mỗi action

**Ngày:** 2026-10-29  
**Dự án:** AI Commerce — `trung-via/ai-commerce`  
**Human authority:** Quyết định mới nhất trong hội thoại AI Commerce 2026-10-29.  
**Nguồn gốc:** WorkItem `EF01-DIRECT-GIT-PYTEST-003`, Issue #81, PR #82.  
**Trạng thái:** HUMAN-ADOPTED TARGET / RUNTIME IMPLEMENTATION NOT YET VERIFIED.  

## Quyết định cuối cùng — supersedes 12+2

**Tất cả các EF01 action** (kể cả các action do EF02 dispatch) **mặc định dùng `pytest -n 12` riêng cho từng action**. Không có primary/secondary; không có host-global một suất 12; không cấp `-n 2` cho action đến sau chỉ vì song song.

| Số action đang thực sự verify đồng thời | Workers / action | Tổng pytest workers |
|---:|---:|---:|
| 1 | 12 | 12 |
| 2 | 12, 12 | 24 |

**Giữ nguyên EF02 `max_parallel=2` mặc định**. `max_parallel` là giới hạn số EF02 *action* đồng thời của batch, hoàn toàn khác với pytest worker count. Công thức cho `N` action thực tế đang verify: `12 × N` pytest workers; trong một batch với `max_parallel=2` có thể đạt **24**. Các batch đồng thời khác (nếu có) cần được báo cáo trung thực; không suy diễn `max_parallel` thành một trần toàn host.

Human xác nhận tài nguyên máy đủ để chạy 24 pytest workers cùng lúc, và trước đây đã thử các mức 2/4/8/12/16, trong đó 12 tối ưu tốc độ. Đây là **HUMAN_REPORTED_PRIOR_BENCHMARK/RESOURCE_CONFIDENCE**, không phải benchmark hay stress test mới từ runtime. **Không đặt lại gate benchmark/host autotuning**.

## Cách triển khai

1. Mặc định áp dụng cho local EF01 và EF02→EF01 verify, EF01-R reverify, fan-in/integration verification, trên Windows host khi có xdist. Pytest argv có `-n 12` **cho từng action**, kể cả action đến sau.
2. Không thêm primary/secondary allocator, host-wide lease, ưu tiên theo thứ tự vào, hoặc cơ chế tự hạ worker theo mức song song. Không tạo thiết kế quá mức để cấp phát 12+2.
3. Chỉ thực hiện kiểm tra nhẹ: `pytest-xdist` được cài/có thể import, 12 subprocess khởi tạo được, isolated temp/fixture không gây lỗi; nếu có `XDIST_UNAVAILABLE`, `WORKER_START_FAILED`, hoặc sự cố thiếu bộ nhớ/tài nguyên thực tế thì ghi evidence và dùng fallback serial hợp lệ hoặc STOP. Không ngầm đổi thành 2/4/8 workers vì CPU đang bận hoặc action khác đang chạy. Khi fallback, kết quả scoped verify có thể PASS nhưng đánh dấu `12_WORKERS_NOT_EXECUTED` rõ ràng.
4. MT-01 quyết định **subset test tối thiểu đủ AC**; `-n 12` quyết định tốc độ chạy subset đó. Không đánh đổi coverage, không suy luận PASS khi thiếu test, no-tests/internal error/timeout hoặc bằng chứng không đủ.
5. Giữ EF02 `max_parallel=2`, khả năng cấu hình đã có, và `verification_profile` registered-only. Không cho tùy ý shell/argv, không sửa trực tiếp protected `.github/` qua worker. Nếu thay đổi registry cần PR riêng ngoài EF02.
6. Ghi `workers_requested=12`, `workers_actual`, `max_parallel`, verify argv, test counts, exit, degradation reason khi có. Không tuyên bố 24 chạy live khi chưa có hai execution span trùng thời gian.

## Điều kiện nghiệm thu

- Một action: actual pytest invocation `-n 12` và scoped PASS/FAIL đúng.
- Hai action verify đồng thời: cả hai `-n 12`, tổng 24 worker theo cấu hình; không có `12+2` hoặc primary/secondary.
- Khi một action fail→repair→reverify: mọi lần gọi pytest đủ điều kiện tiếp tục `-n 12`; EF01-R giữ đúng executor/model/effort và test evidence.
- Fan-in chạy aggregate consumer/integration tests, không lấy PASS riêng lẻ làm PASS chung, dùng `-n 12` nếu đang ở Windows host và xdist khả dụng.
- Lỗi thiếu xdist/worker launch hoặc test fixtures không song song được: kết quả minh bạch, không tự nhận đã chạy 12.
- Không benchmark lại hoặc tăng `max_parallel` để thử 3 action; không gate Roadmap A.

## Trạng thái Issue #81 / PR #82

Issue #81 được tạo trước khi Human cập nhật quyết định; bản `request.json` mà workflow nhận là immutable. Comment sửa quyết định **không tự thay đổi snapshot**. Nếu worker đã thực thi với prompt cũ, đối chiếu candidate, thực hiện một patch/attempt tiếp theo được ủy quyền khi cần; không tự động replay attempt hay xác nhận run đã cập nhật.

PR #82 chỉnh WorkItem và quyết định này; merge và kích hoạt runtime chỉ sau khi thực sự kiểm chứng. **12+2 bị bãi bỏ** hoàn toàn.
