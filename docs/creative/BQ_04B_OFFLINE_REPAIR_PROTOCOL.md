# BQ-04b — Ranh giới sửa ngoại tuyến và bằng chứng

Phiên bản `1.0.0`; base `13e021e0b45cd7a19872144d986473054c68bd85`.
Trạng thái: **POLICY_ONLY_HOLD**. BQ-04a tiếp tục là thẩm quyền chính sách/lập kế hoạch; không sửa module đóng đối soát của BQ-04a.

Lát cắt này chưa có phép sửa media thực thi được. Q4 BQ02b đo tín hiệu trên byte thật, nhưng không tự xác định tín hiệu đen/trắng/im lặng là lỗi sáng tạo. Timestamp chỉ là thời gian yêu cầu seek, chưa đo frame/PTS gốc hoặc mặt nạ/vùng không gian; quyền vẫn UNKNOWN. Không lấy `defect=true` do caller hoặc test chèn vào làm bằng chứng lỗi. Chưa có oracle độc lập kiểm tra mục tiêu và mọi bất biến kỹ thuật ngoài mục tiêu. Vì vậy dùng nhánh HOLD đã được Human cho phép khi không thể triển khai sửa an toàn trong đúng phạm vi ADD_ONLY.

Adapter chỉ nhận native `BQ04aRepairDecision` và `BQ04aPreservationPlan` cùng digest và ngữ cảnh replay. Không nhận đường dẫn, media private, callback editor, runner, shell/script, hoặc tự khai quyền. Không mở/ghi file, không tạo scratch hay output. Biên nhận `BQ04bOfflineAttemptReceipt.v1` bất biến ghi HOLD, SHA gốc theo native Q4, SHA output và vị trí thực tế bằng null. Không tự nhận đã hash lại byte hiện tại. Không có no-op giả thành cải thiện.

Sidecar `BQ04bTechnicalVerification.v1` chỉ đối chiếu native BEFORE/AFTER tool-backed, report A13, chuỗi SHA/digest, request tái kiểm định gốc và biên nhận. Không thực thi I/O; byte hiện tại vẫn UNKNOWN. SHA khác hoặc media sạch khác không chứng minh liên kết với lỗi và phép sửa. Kết quả luôn `IMPROVEMENT_UNPROVEN` và `PRESERVATION_UNPROVEN` cho phạm vi hiện tại; mọi invariant vẫn bật nhưng chưa được chứng minh bảo toàn. Không coi hash hoặc native object trong cùng process là chữ ký/chứng thực độc lập.

Test tạo MP4 tổng hợp bằng FFmpeg qua runner A13 có giới hạn, rồi chạy A13 và BQ02b thật. Nhãn kết quả là **SYNTHETIC**. Đây chỉ là bằng chứng cơ chế kỹ thuật và fail-closed; không phải `OBSERVED_PRODUCTION`, Brain/media PASS hay cải thiện media sản xuất. Quyền hình/giọng/diễn viên, sự thật sản phẩm và claims, câu chuyện/ngữ nghĩa và hiệu chuẩn Human luôn UNKNOWN nếu chưa có bằng chứng riêng. Im lặng có chủ ý, hư cấu, hoạt hình và `product_ref=null` tiếp tục hợp lệ; không phát minh lỗi từ các miễn trừ đó.

P0, thiếu/sai quyền, UNKNOWN_EFFECT, thiếu lỗi gốc hoặc shot/time/mask, sai parent/version, oracle thiếu, invariant bị tắt, editor không hỗ trợ, caller tự khai hoặc text độc hại không thể cho phép sửa. Replay so toàn bộ schema canonical, từ chối trường thêm/thiếu, SHA output tự bịa, tự nhận observed, no-regression, quality pass, quyền phát hành/chi phí. API không có cửa nhận path nên traversal/link/hardlink/TOCTOU không mở được đường ghi file. Sidecar không phát hiện file đã đổi sau lần đo; nêu rõ UNKNOWN, không giả chứng minh nguồn còn nguyên.

Không gọi provider trả phí/tạo sinh, PULSE runtime, Chrome/HostOps, mạng, media private hoặc nghiên cứu Human. Không cấp quyền rights/spend/publish; không dùng Issue/PR làm grant. Issue #347 là DRAFT nghiên cứu; #495 và PR #502 chỉ là nguồn lịch sử. Nguồn live chưa đọc lại khi mạng bị cấm phải ghi UNKNOWN.

Muốn sửa thật sau này cần ủy quyền riêng, bằng chứng lỗi/định vị native, quyền đúng đối tượng, đúng byte gốc, đúng thao tác/version và oracle độc lập; chỉ tạo byte mới trong scratch mới sở hữu dưới test temp, kiểm tra file thật để có SHA output khác, giữ nguyên gốc, rồi kiểm tra mục tiêu và các bất biến ngoài mục tiêu. Không mở rộng phạm vi hiện tại để giả đạt điều kiện đó.

Executor chỉ thêm bảy file được chỉ định. EF01/EF02 phải xác minh scope/hash/MT01 độc lập trước khi EF02 xuất một CANDIDATE_BRANCH; Windows hosted PR CI và Brain review là các cổng riêng. Chỉ Brain PASS thật mới có thể kích hoạt trusted auto-merge. Không có tác động sửa media production được cấp bởi GitHub WorkItem này.
