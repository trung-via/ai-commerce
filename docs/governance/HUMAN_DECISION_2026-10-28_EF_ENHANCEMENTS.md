# Human decision — Bổ sung cải tiến EF01/EF02 sau M0-R

**Ngày:** 2026-10-28  
**Dự án:** AI Commerce độc lập  
**Trạng thái:** HUMAN-APPROVED FOR ROADMAP / PLANNED_NOT_IMPLEMENTED

## Quyết định

Human chấp thuận đưa ba cải tiến vào **cùng** Work Tree v1.1 và Roadmap A v1.0, dưới hai WBS có sẵn **EF01/EF02**; không tạo WBS mới, không tăng 63 baseline IDs và không mở lại M0-R. Đây là sự chấp thuận **lập kế hoạch**, không khẳng định đã chạy executor, được cấp kênh ghi vào chat, đã có 12 pytest workers hoặc đã cài Direct Git.

**M0-R hiện hành:** `ACCEPTED_WITH_DEFERRED_RISKS` tại WorkItem EF02; lỗi runner-service thật và các negative-path chưa live vẫn UNKNOWN/deferred. Không biến UNKNOWN thành PASS; vẫn giữ no-blind-replay.

## Backlog và vị trí triển khai

| Thứ tự | WorkItem | WBS owner | Ưu tiên | Thời điểm | Sản phẩm |
|---|---|---|---|---|---|
| 1 | `EF01-VERIFY-REPAIR-002` | EF01; EF02 nhận aggregate | P1 | Phase 1 song song A03/A04, trước khi số lượng task code tăng mạnh | Executor → Verify → phân loại lỗi → cùng executor sửa → Verify lại; trả một kết quả action |
| 2 | `EF02-EXACT-CHAT-WAKE-002` | EF02-D (tách khỏi core) | P1 | Phase 1 khi có consumer chat binding thực | Completion Event → bridge → chính xác chat gốc → Brain sync; fallback GitHub |
| 3 | `EF01-DIRECT-GIT-PYTEST-003` | EF01, phối hợp EF02 host slots | P2 | Sau khi có baseline test và đo tài nguyên Windows; Phase 1/2 | Direct Git executable tùy chọn; benchmark pytest-xdist đến 12 workers theo năng lực host |

Đây là *thứ tự ưu tiên*, **không phải dependency bắt buộc**: EF02-D chỉ cần EF02-C kết quả cuối cùng, không chờ EF01 repair; performance có thể thử độc lập; A03/A04/A05 và các phase tiếp theo không phải chờ các cải tiến này.

## Bất biến kỹ thuật

- EF01-R: một **EF02 action/claim duy nhất**, nhưng mỗi lần gọi CLI nội bộ cần `invocation_id`/lượt riêng, logs/usage/evidence riêng; không tự coi đây là resume cùng model session. Mặc định đề xuất tối đa 2 lượt sửa, cấu hình được. Chỉ sửa khi executor đã return bình thường và **verify code** FAIL với bằng chứng rõ. Truyền exit code, stdout/stderr, diff, paths/hash và hạn chế scope đến **chính executor/model/effort đã chọn**. Dừng cho `EXECUTION_UNKNOWN`, timeout, transport ambiguity, model unavailable/quota, quyền, scope, source drift, Git publication failure; không re-invoke trong trường hợp side effects bất định. Một action chỉ công bố candidate sau lần verify đạt và scope/hash chính xác. Không vô tình tạo hai claim.
- EF02-D: GitHub comment/artifact **không** phải bằng chứng đã đánh thức Brain. Wake chỉ được đánh dấu `DELIVERED_TO_EXACT_CHAT` khi chat binding được phép, có endpoint/connector phù hợp, có ack hoặc bằng chứng UI thực; match conversation/context và chống lặp. Không tự tái tạo chat, giả message, truy cập token/cookie trái quyền, hoặc mang AIOS-Renew runtime. GitHub status là fallback khi không có hỗ trợ.
- EF01-P: mã hiện gọi Git trực tiếp bằng argv `git`; nâng cấp là *tuỳ chọn cấu hình executable path* và đo, không hardcode Git Windows hoặc tắt Defender. `pytest-xdist` chưa có trong dev dependencies; 12 workers là **mục tiêu thử nghiệm**, không bắt mọi test/runner phải chạy 12. Phải phân biệt `pytest -n 12` với `EF02 max_parallel=2`; giới hạn tổng tải CPU/RAM và việc nhiều EF02 actions cùng dùng xdist. Giữ profile pytest tuần tự làm fallback.
- Không thay đổi quyền Human đối với executor, model, effort; không auto merge main, không xuất bản media sản phẩm, không thêm cổng approval/benchmark tùy tiện, không import AIOS-Renew.

## Vùng mã đề xuất khi bắt đầu implementation

- `EF01-R`: `src/ai_commerce/engineering/runner.py`, `cli.py`, `tests/test_ef01.py`; EF02 worker mapping/result compatibility `src/ai_commerce/remote/worker.py` và tests tương ứng. Không đổi GitHub attempt identity để tự lách dedup.
- `EF02-D`: `src/ai_commerce/remote/`, bounded completion event/transport, GitHub feedback reader, bridge adapter riêng chỉ nếu có endpoint được hỗ trợ; test có consumer chat thật trước khi tuyên bố same-chat success.
- `EF01-P`: `src/ai_commerce/engineering/runner.py`, `src/ai_commerce/remote/worker.py`, `.github/ef02/verify-profiles.json`, `pyproject.toml` và test; không mở rộng CI bắt buộc 12 workers nếu thiếu năng lực thực tế.

## Kết quả của quyết định này

Chỉ tạo backlog, điều chỉnh Work Tree/Roadmap và WorkItems. **Không dispatch, không gọi model, không thực hiện đăng nhập hoặc wake vào chat, không thay runner service.** Điều kiện nghiệm thu riêng nằm trong ba WorkItem; implement sau trong những PR có chứng cứ riêng.
