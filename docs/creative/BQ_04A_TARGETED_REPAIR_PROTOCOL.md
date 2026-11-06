# BQ-04a — Giao Thức Q5/R7: Sửa Đổi Đa Phương Tiện Có Mục Tiêu, Bảo Toàn Bất Biến Và Tái Kiểm Định (Targeted Media Repair Protocol)

Phiên bản: `1.0.0` | Kiến trúc: `PA2/PULSE` | Gói: `BQ-04a (Q5/R7)` | Trạng thái: `OFFLINE_SOURCE_SPECIFICATION`

---

## 1. Mục Đích Và Triết Lý Cốt Lõi

Giao thức này quy định quy trình và chính sách đưa ra quyết định sửa đổi đa phương tiện có mục tiêu (Q5 - Targeted Media Repair), xác lập phạm vi bảo toàn bất biến (Preservation Plan), và yêu cầu tái kiểm định đối chứng (Q4 Re-inspection) theo chuẩn R7.

### Nguyên Tắc Bất Di Bất Dịch:
1. **Quy Hoạch Ngoại Tuyến (Offline Plan Only):** BQ-04a thuần túy là bộ máy suy luận kế hoạch, đưa ra quyết định có căn cứ và ràng buộc bảo toàn; **tuyệt đối không thực thi chỉnh sửa media, không gọi công cụ dựng hình (renderer), không gọi mô hình tạo sinh (generative provider), không gọi PULSE runtime hay giải ngân chi phí.**
2. **Không Tự Động Phê Duyệt Xuất Bản & Không Khẳng Định Cải Thiện Giả Tạo (No False Pass / No Unproven Improvement):** Điểm kiểm định xanh từ Q4 hay đề xuất sửa đổi không bao giờ tương đương với giấy phép phân phối (`GO_PUBLISH`). Đặc biệt, việc media sau sửa có byte/SHA khác hoặc có biên nhận tự xưng không chứng minh được khiếm khuyết đã khắc phục hay bất biến không bị suy giảm; trạng thái tái kiểm định luôn là `IMPROVEMENT_UNPROVEN` và `PRESERVATION_UNPROVEN` cho đến khi có đo lường trực tiếp có xác thực (BQ-04b). Thẩm quyền phát hành luôn thuộc về Human và Brain độc lập.
3. **Phân Định Thẩm Quyền Nghiêm Ngặt:**
   - **Brain (ChatGPT duy nhất):** Nắm giữ thẩm quyền tối cao về mục tiêu nghệ thuật, thông điệp thương mại, sự thật sản phẩm và phê duyệt chi phí/chiến lược.
   - **Q2 (Adversarial Critic):** Đặt câu hỏi phản biện, nêu rủi ro P0/P1/P2/P3 trước và sau kiểm định.
   - **Q4 (Multimodal Media Quality Inspector):** Đo lường kỹ thuật thực tế (T1/T2), phân loại phát hiện (findings) kèm tọa độ thời gian/không gian và mức độ bất định.
   - **Q5 (Targeted Media Repair):** Chọn hướng xử lý tối thiểu (KEEP, LOCAL EDIT, REGENERATE, RETURN_TO_BRAIN, HOLD) và khóa chặt vùng bất biến.
   - **PULSE Runtime:** Chỉ điều phối khi có ủy quyền độc lập và cơ chế lưu ký riêng (B0-S).
4. **Fail-Closed Đối Với Rủi Ro P0:** Mọi vi phạm về quyền (bản quyền hình ảnh, giọng nói, nhạc), sự thật sản phẩm (tuyên bố sai lệch, công dụng giả định), hoặc danh tính pháp lý đều dẫn tới quyết định `HOLD` lập tức, không được phép thử lại tự động hay tự ý sửa chữa.

---

## 2. Quy Trình 7 Bước Q5/R7 (The 7-Step Repair Lifecycle)

```
[Q4 Inspection Findings] (T1/T2 thật + T3 chưa chạy)
          │
          ▼
   [Bước 1: Q4 Finding Intake] ──► Kiểm tra tính xác thực, SHA gốc, tọa độ lỗi
          │
          ▼
   [Bước 2: Repair Choice] ──► KEEP | LOCAL_EDIT | SHOT_REGENERATE | RETURN_TO_BRAIN | HOLD
          │
          ▼
   [Bước 3: Freeze Invariants] ──► Khóa chặt sản phẩm, nhân vật, âm thanh, bối cảnh
          │
          ▼
   [Bước 4: Scoped Authorization] ──► Giới hạn không gian, thời gian, ngân sách, quyền
          │
          ▼
   [Bước 5: Future Output SHA] ──► Chỉ ghi nhận hash kỳ vọng trong tương lai (không giả mạo)
          │
          ▼
   [Bước 6: Q4 Re-inspection] ──► Đo lại độc lập sau sửa chữa (T1/T2 sau sửa đổi)
          │
          ▼
   [Bước 7: Q2 Challenge & Brain Review] ──► Đối chiếu rủi ro và chuyển Brain quyết định
```

### Bước 1: Tiếp Nhận Phát Hiện Từ Q4 (Q4 Finding Intake)
- Chỉ chấp nhận đầu vào là thực thể `BQ02bMultimodalReview` chuẩn hóa gốc và các chuỗi băm bằng chứng (`digest`, `asset_sha256`) đã được ghim.
- Tuyệt đối từ chối đầu vào dạng từ điển tự do (arbitrary dict/JSON) hoặc các phát biểu chưa được công cụ đo kiểm chứng thực.
- Khiếm khuyết do người gọi tự khai báo (`grounded_defect` trong context) nếu thiếu dữ liệu đo lường kỹ thuật thực tế từ Q4 (`measured_findings` hoặc `technical_evidence.findings`) bắt buộc phải chuyển về `RETURN_TO_BRAIN` (`UNPROVEN_CALLER_DEFECT_NO_NATIVE_OBSERVATION`), tuyệt đối không được tự động thăng hạng thành lỗi để đề xuất sửa chữa cục bộ.
- Tách bạch rõ: Đo lường kỹ thuật quan sát được (T1 qua A13, T2 qua tín hiệu khung hình/âm thanh) và các phần chưa chạy (`T3_NOT_RUN`, `SEMANTIC_UNKNOWN`).

### Bước 2: Lựa Chọn Hướng Sửa Đổi (Repair Choice)
Hệ thống Q5 đưa ra duy nhất một trong năm lựa chọn khép kín:
1. `KEEP_ORIGINAL`: Giữ nguyên tài sản gốc. Áp dụng khi khiếm khuyết không đáng kể, mang tính phong cách nghệ thuật, hoặc chi phí/rủi ro sửa chữa vượt quá lợi ích. Không ghi nhận nhãn "quality-pass" giả tạo.
2. `PROPOSE_LOCAL_DETERMINISTIC_EDIT`: Đề xuất chỉnh sửa cục bộ tất định (ví dụ: cắt tỉa đầu cuối vài frame, cân chỉnh âm lượng, chuẩn hóa dải màu, che mờ vùng nhỏ). Không kích hoạt chỉnh sửa tại bước này.
3. `PROPOSE_SHOT_REGENERATION`: Đề xuất tạo lại toàn bộ shot quay khi lỗi không thể khắc phục cục bộ. Đòi hỏi ủy quyền chi phí và phân bổ tài nguyên mới từ Human/Brain. Không tự động phát lệnh gọi nhà cung cấp (provider dispatch).
4. `RETURN_TO_BRAIN`: Trả về Brain khi lỗi liên quan đến logic cốt truyện, tính vật lý bất khả thi ngoài dự kiến, thông điệp thương hiệu sai lệch hoặc sở thích chủ quan P3 của Q2.
5. `HOLD`: Đóng băng quy trình khi có vi phạm P0 (quyền sở hữu, sự thật sản phẩm, danh tính), khi không xác định được vị trí lỗi cụ thể (lack of grounded fault localization), hoặc khi tác động của nhà cung cấp chưa rõ (`UNKNOWN_EFFECT`).

### Bước 3: Đóng Băng Các Bất Biến (Freeze Non-Target Invariants)
Mỗi kế hoạch sửa chữa phải định nghĩa tường minh danh sách các bất biến bắt buộc phải giữ nguyên vẹn 100%:
- **Sản Phẩm (Product Identity & Claims):** Hình dạng, nhãn mác, màu sắc sản phẩm thật, các công bố công năng phải giữ nguyên, không được bóp méo hay làm mờ.
- **Quyền & Nhân Vật (Likeness & Rights):** Khuôn mặt, giọng nói, trang phục của diễn viên được cấp phép; không thay thế hay biến dạng người biểu diễn.
- **Tính Liên Tục (Continuity Invariants):** Chiều hướng ánh sáng, màu sắc môi trường xung quanh vùng chỉnh sửa, tính liên tục của âm thanh nền và nhịp điệu thời gian.
- **Khu Vực Ngoài Phạm Vi (Spatial & Temporal Boundaries):** Toàn bộ khung hình ngoài vùng ROI (Region of Interest) và ngoài khoảng thời gian [t_start, t_end] phải giữ nguyên bit-for-bit khi thực hiện sửa cục bộ.

### Bước 4: Giới Hạn Phạm Vi Ủy Quyền (Scoped Authorization)
- Kế hoạch bảo toàn (`Preservation Plan`) phải nêu rõ: tọa độ thời gian (giây/frame), tọa độ không gian (tỷ lệ chuẩn hóa `[x_min, y_min, x_max, y_max]`), shot ID chịu ảnh hưởng.
- Nếu không thể xác định vị trí lỗi cụ thể, hệ thống không được bịa đặt tọa độ giả định mà phải chuyển về `HOLD` hoặc `RETURN_TO_BRAIN`.

### Bước 5: Quản Lý Hash Kỳ Vọng (Future Artifact SHA Tracking)
- Hệ thống chỉ lưu giữ `original_asset_sha256` của media gốc.
- Đối với media sau sửa chữa, hệ thống **không bao giờ tự bịa ra SHA đầu ra**. Trường `expected_derived_sha256` hoặc danh sách hash sau chỉnh sửa chỉ được điền khi có bằng chứng tạo file thực tế từ quy trình dựng độc lập.

### Bước 6: Tái Kiểm Định Độc Lập Đối Chứng (Q4 Re-Inspection)
- Mọi sửa đổi phải được chứng minh bằng một đợt kiểm định Q4 mới hoàn toàn độc lập (`AFTER` measurement).
- Nếu kế hoạch bảo toàn ở trạng thái `HOLD` hoặc `RETURN_TO_BRAIN`, yêu cầu tái kiểm định lập tức chuyển sang trạng thái không khả thi (`HOLD_...`, `RETURNED_TO_BRAIN_NON_ACTIONABLE`), và bản đối soát đóng bị chặn hoàn toàn (`HOLD_REINSPECTION_BLOCKED`, `outcome="NON_ACTIONABLE_REQUEST"`).
- Nếu chưa có tài sản mới hoặc chưa chạy đo lường thực tế, trạng thái bắt buộc là: `PENDING_REINSPECTION`, `IMPROVEMENT_UNPROVEN`, `PRESERVATION_UNPROVEN`.
- Không chấp nhận suy luận: đo lường kỹ thuật T1/T2 đạt không chứng minh được ngữ nghĩa hay sự hài lòng của con người được cải thiện. Media thay thế có SHA khác không chứng minh được sửa chữa thành công hay không thoái lui bất biến.

### Bước 7: Đối Soát Phản Biện Q2 Và Chuyển Brain Đánh Giá (Q2 Challenge & Brain)
- Bản đối soát sửa chữa (`Repair Closure`) lưu giữ toàn bộ khiếm khuyết còn tồn đọng (regressions), các trường hợp phản chứng (counterexamples).
- Brain là thực thể duy nhất xem xét báo cáo tổng hợp và đưa ra quyết định chấp nhận rủi ro hay hủy bỏ.

---

## 3. Phân Loại Khiếm Khuyết (Fault Taxonomy)

| Nhóm Khiếm Khuyết | Mã Định Danh | Mô Tả Kỹ Thuật | Hướng Xử Lý Mặc Định |
|---|---|---|---|
| **Kỹ Thuật T1 (Decoding/Corrupt)** | `T1_DECODE_FAILURE`, `T1_CONTAINER_INVALID` | Lỗi giải mã container MP4, hỏng luồng moov, không đồng bộ frame | `PROPOSE_LOCAL_DETERMINISTIC_EDIT` hoặc `HOLD` |
| **Tín Hiệu T2 (Black/Freeze/Audio)** | `T2_FREEZE_FRAME`, `T2_AUDIO_CLIPPING`, `T2_UNINTENDED_SILENCE` | Khung hình đóng băng bất thường, âm thanh vượt ngưỡng méo tiếng, mất tiếng | `PROPOSE_LOCAL_DETERMINISTIC_EDIT` (nếu cục bộ) |
| **Quyền & Pháp Lý P0 (Veto)** | `P0_RIGHTS_MISSING`, `P0_CLAIM_MISMATCH`, `P0_IDENTITY_VIOLATION` | Thiếu bản quyền nhạc/diễn viên, thổi phồng công dụng sản phẩm, vi phạm nhãn hiệu | `HOLD` (Bắt buộc dừng ngay lập tức) |
| **Ý Đồ Kịch Bản P1 (Plan Revision)** | `P1_CAUSAL_BREAK`, `P1_TEMPORAL_INCONSISTENCY` | Hành động phi logic so với kịch bản, vật thể biến mất không lý do | `PROPOSE_SHOT_REGENERATION` hoặc `RETURN_TO_BRAIN` |
| **Ngữ Nghĩa / Thẩm Mỹ P2/P3** | `P2_AESTHETIC_UNSTABLE`, `P3_SUBJECTIVE_PREFERENCE` | Sở thích cá nhân về tông màu, nhịp điệu nhanh/chậm | `KEEP_ORIGINAL` hoặc `RETURN_TO_BRAIN` (Không tự động sửa) |
| **Không Rõ Tác Động** | `UNKNOWN_PROVIDER_EFFECT`, `UNGROUNDED_FAULT` | Lỗi mơ hồ từ API ngoài, không định vị được frame/vùng lỗi | `HOLD` (Không thử lại vô hạn) |

---

## 4. Các Trường Hợp Miễn Trừ Hợp Lệ (Valid Exemptions)

Hệ thống Q5/R7 tôn trọng các trường hợp sáng tạo đặc thù, không kích hoạt báo động sai:
1. **Sản Phẩm Null (`product_ref = None`):** Các video thuần túy xây dựng nhận diện thương hiệu, tạo cảm xúc hoặc giới thiệu phong cách sống không chứa sản phẩm cụ thể là hoàn toàn hợp lệ.
2. **Âm Thanh Im Lặng (`Silent / Ambient Video`):** Đoạn video cố tình để tĩnh lặng nhằm tạo khoảng lặng cảm xúc hoặc phù hợp môi trường tự động tắt tiếng (autoplay muted) không bị coi là lỗi mất tiếng (`T2_UNINTENDED_SILENCE`).
3. **Hoạt Họa & Giả Tưởng (`Animation & Fantasy Physics`):** Các hiệu ứng vật lý cường điệu, bay lượn, phép thuật hoặc đồ họa hoạt hình không bị coi là vi phạm tính chân thực (`R1_UNREALISTIC_PHYSICS`).

---

## 5. Ranh Giới Giữa BQ-04a Và BQ-04b

- **BQ-04a (Hiện Tại):** Thuần túy là tầng logic ra quyết định, lập kế hoạch bảo toàn và giao thức tái kiểm định. Không đụng chạm file nhị phân, không chỉnh sửa media, không phụ thuộc ffmpeg/opencv trong runtime chính.
- **BQ-04b (Giai Đoạn Kế Tiếp):** Bộ công cụ thực thi chỉnh sửa tất định cục bộ (Deterministic Local Editor) trên môi trường cô lập, chỉ nhận lệnh khi có `BQ04aPreservationPlan.v1` hợp lệ và được ký duyệt.

---

## 6. Giả Thuyết Về Chi Phí & Độ Trễ (Cost & Latency Hypotheses)

Các giả định rằng: "Chỉnh sửa cục bộ tiết kiệm 80% chi phí so với tạo lại shot", hay "Sửa đổi mục tiêu giảm độ trễ 5 lần" **hoàn toàn là các giả thuyết chưa được kiểm chứng trong môi trường sản xuất (UNTESTED HYPOTHESES)**. BQ-04a không đưa các tuyên bố chưa chứng minh này vào làm căn cứ tính toán định lượng runtime.

---

## 7. Kết Luận

BQ-04a thiết lập một rào chắn kiểm soát chất lượng có kỷ luật cao, ngăn ngừa việc "chữa lợn lành thành lợn què" (regression), bảo vệ tối đa quyền tài sản và sự thật sản phẩm, đồng thời duy trì quyền định đoạt tối thượng của con người và Brain trong toàn bộ hệ thống AI Commerce.
