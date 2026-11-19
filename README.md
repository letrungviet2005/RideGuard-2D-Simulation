# RideGuard Sensor Lab và công cụ dữ liệu

Cập nhật 19/11/2026. Tool desktop Python/PySide6 đã có mô phỏng 2D,
replay radar CSV và importer BMI160, Serial client, TX/RX JSONL và đo nhịp RESULT.
Firmware `sensor_simulation` xử lý bản tin JSON và trả RESULT ở 921600 baud.
Firmware `sensor_logger` trong source phục vụ thu radar/IMU thật. Bo từng có
banner logger460800; lượt kiểm chứng mới nhất đã ACK sensor_simulation ở921600.
Chưa đọc flash để xác minh binary/hash đang nạp; nhiệm vụ không tự nạp lại.

- [Hướng dẫn Sensor Lab](HUONG-DAN.md): cài/chạy từng bước, ý nghĩa từng ô input, nhập CSV và đọc lỗi/log.
- [AI và so sánh hai view](../docs/ai/sensor-lab.md): dataset v1, môi trường AI riêng, train/evaluate, cùng một stream, xem lại đồng bộ sau STOP.
- [Timeline và chấm với nhãn](../docs/ai/so-sanh-timeline.md): sáu dòng ESP32/AI/nhãn trái–phải, nhảy đoạn bất đồng sau STOP, HIGH đầu/tổng HIGH, FN/FP trên các mốc chung hợp lệ; chạy đủ split test mà không train lại.
- [Model card](../docs/ai/model-card-synthetic-v1.md) và [kiểm chứng 19/11](../docs/testing/ai-comparison-2026-11-19.md): kết quả tổng hợp, tách test PC/Qt/ESP32 thật và giới hạn.
- [Chẩn đoán lỗi IMU](../docs/testing/imu-error-diagnostics-2026-11-18.md): thông báo có dòng/cột, timestamp, băng thông và log lỗi trước START; 133 test PC đạt.
- [CSV IMU14cột](rideguard-simulator/IMU-CSV.md): profile theo bảng người dùng, generator, µs/offset/mount, UI và băng thông.
- [Kiểm chứng CSV và bo](../docs/testing/imu14-2026-11-18.md): lượt trước có 128 test PC, chọn file trên GUI thật, replay 9000 mẫu/899 RESULT trên ESP32 tại 0,5×.
- [Bộ firmware và hướng dẫn nạp tay](../docs/testing/manual-firmware-2026-11-18.md): merged BIN simulator/logger, SHA-256, partition và kiểm tra ACK. Backup tự động đã dừng; chưa nạp các binary bàn giao.
- [Thu log thật](rideguard-simulator/THU-LOG-THAT.md): wiring và thu đồng bộ.
- [Kiểm tra bo và CSV](../docs/testing/sensor-lab-recheck-2026-11-18.md).
- [Chuyển firmware có backup và nghiệm thu](../docs/testing/logger-to-simulation-2026-11-18.md): trạng thái mới nhất của lượt chuyển đổi, manifest/restore và test.

Lượt 18/11 có 133 test Python về simulator/chẩn đoán lỗi; lượt 19/11 bổ sung AI và replay thật trên ESP32, xem báo cáo có ngày ở trên. Điều này chưa xác nhận cảm biến thật
hay hoàn tất nghiệm thu trên firmware mới. CSV trong `samples/generated` là
giả lập. File `empty_frames_45s_dual.csv` không có khung rỗng; không dùng tên
file làm bằng chứng. Giữ log, backup flash và thông tin thiết bị trong `.local`
ngoài Git. Contract checker của monorepo nằm trong `scripts/`.
