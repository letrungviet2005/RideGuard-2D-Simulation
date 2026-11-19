# CSV IMU 14 cột — chạy và hiểu đúng dữ liệu

Ngày cập nhật 18/11/2026. [Báo cáo test PC, GUI và ESP32](../../docs/testing/imu14-2026-11-18.md). Các số đo cũ là lịch sử; lần này COM5 đã ACK giao thức ứng dụng tại 921600, chưa xác minh hash firmware đang trên bo. Không tự upload/backup/erase.

## Mở app

Xem [hướng dẫn thao tác đầy đủ](../HUONG-DAN.md) để biết ý nghĩa từng ô input, thứ tự nhập radar/IMU và cách đọc lỗi. Lỗi nhập/START có chi tiết và được lưu riêng trong `.local/simulator-logs/app-errors-*.jsonl` ngay cả trước START. `64.985 ms` ở phần tóm tắt là thời lượng, không phải lỗi. [Báo cáo chẩn đoán](../../docs/testing/imu-error-diagnostics-2026-11-18.md).

PowerShell từ **gốc repository**:

```powershell
.\scripts\run-simulator.ps1
```

Từ **tools/rideguard-simulator**:

```powershell
.venv/Scripts/python.exe -X utf8 -m rideguard_sim
```

Hai lệnh đã mở GUI thật. Dùng venv/dependency hiện có; không cần backend/Docker/mobile. Chỉ một app/harness giữ COM; không kết nối GUI khi harness đang chạy.

## Schema/profile

Header strict đúng thứ tự, không thêm cột:

```text
timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C
```

`user_table_v1`: timestamp nguyên không âm tăng nghiêm ngặt; sáu RAW int16 có dấu; sáu giá trị vật lý và nhiệt độ hữu hạn. Kiểm `physical ≈ raw × scale`, dung sai 0,0000051 cho cột tham chiếu làm tròn 5 số lẻ. Scale gia tốc **0,000122 g/LSB**, gyro **0,0175 °/s/LSB**, lấy từ bảng người dùng; **chưa xác minh cấu hình phần cứng hay model cảm biến**. Nhiệt độ là nhiệt độ cảm biến, mặc định tổng hợp 29,75°C, không phải nhiệt độ môi trường.

`physical_only` là lựa chọn tương thích cho mẫu cũ/log capture thiếu RAW, chỉ dùng cột vật lý, kiểm kiểu/range nhưng **không kiểm quan hệ RAW/đơn vị**. Không áp profile mới vào file cũ `imu_bmi160_*.csv` có scale khác rồi coi mismatch là lỗi chip. Python `load()` giữ mặc định compatibility; GUI/generator mới mặc định `user_table_v1`.

CSV nguồn không sửa: giữ timestamp µs ban đầu và thời gian µs đã chọn mốc ở RAM; đổi `g × 9.80665` sang m/s² và giữ gyro dps. Chỉ floor sang ms khi gửi giao thức. Không chuẩn hóa gia tốc về 1g; không trừ trung bình gyro cả phiên; không tự gán nhãn chuyển hướng. Chưa có chức năng bias calibration tự động.

File tham chiếu `../samples/reference/user-provided-14rows.csv` nguồn **USER_PROVIDED**, 14 mẫu, span **64,985 ms**, khoảng 200,046 Hz; không biết chip/cách lắp/nhãn vận động. `az<0` hợp lệ tùy tư thế. Sidecar `<csv>.json` chứa provenance và SHA-256; nếu có sidecar nhưng hash sai, importer từ chối. Thiếu metadata thì nguồn UNKNOWN, không suy đoán “ghi thực”.

## Sinh mẫu mới

Từ gốc repo (chọn tên output mới, generator không ghi đè file có sẵn):

```powershell
& tools/rideguard-simulator/.venv/Scripts/python.exe -X utf8 tools/rideguard-simulator/generate_imu_csv.py --scenario lane_left --duration 45 --rate 200 --seed 17 --profile user_table_v1 --output .local/my-lane-left.csv
```

Từ thư mục simulator:

```powershell
.venv/Scripts/python.exe -X utf8 generate_imu_csv.py --scenario lane_right --output ../../.local/my-lane-right.csv
```

`--scenario`: `stationary` (đứng yên ngang), `straight` (đi thẳng đều), `lane_left`, `lane_right`, `steady_left` (quay trái đều riêng biệt), `bumpy` (rung ngắn, yaw0), `tilted` (đứng yên với body roll25°/pitch−20°). Các file đã tạo đủ trong `../samples/imu14-v1/`, tên `<scenario>-45s-200hz.csv` kèm JSON. Không sửa mẫu cũ.

Mặc định **45 giây, 200Hz, seed17**, lấy mẫu `[0,duration)`: N=duration×rate phải nguyên; 9000 mẫu 0..44995000µs, **span44,995 giây** khác thời lượng danh nghĩa45giây. Timestamp dùng `round(index×1e6/rate)`, tăng nghiêm ngặt; rate tối đa1MHz, giới hạn2triệu mẫu. Profile và mô hình không được đặt tên BMI160 vì chưa có bằng chứng cấu hình chip.

Tùy chọn:

```powershell
.venv/Scripts/python.exe -X utf8 generate_imu_csv.py --scenario stationary --mount-deg 10 -20 0 --temperature 29.75 --accel-noise-g 0.001 --gyro-noise-dps 0.05 --accel-bias-g 0 0 0 --gyro-bias-dps 0 0 0 --seed 42 --output ../../.local/mounted-noisy.csv
```

Mount là **R_body_sensor = Rz(yaw) Ry(pitch) Rx(roll)** (độ) từ cảm biến sang xe; generator áp ma trận chuyển ngược để xuất tín hiệu sensor-frame. Trong app dùng cùng Euler để đổi về xe; nhập tay theo metadata, không tự đoán mounting. Body/world X tiến, Y trái, Z lên. `f_body = R_world_body^T (a_world − gravity_world)`, gravity world=(0,0,−9,80665); ngang đứng yên → (0,0,+9,80665). Mô hình chuyển làn quintic y(t), heading=atan2(vy,vx), gyro từ đạo hàm heading, có pha quay trả hướng. Không kéo gz=+8 thành “chuyển làn”. `steady_left` mới là quay đều8°/s. Rung là gia tốc từ dịch chuyển Z trơn, không tự bịa nhãn cảnh báo.

RAW = round(vật lý/scale), sau đó **tính lại cột vật lý từ RAW đã lượng tử**, seed tái lập. Vượt int16 → báo lỗi/refuse export, không wrap/clip; không xuất một file với saturation giấu kín. Noise/bias được khai báo trong metadata, mặc định0 lý tưởng. Nhiễu sensor được thêm sau phép quay mount; noise/bias là lựa chọn mô phỏng, không phải calibration từ log thực.

## Import, offset và nguồn ghép

1. Dừng phiên/chờ ACK, chọn profile, ánh xạ trục và Euler sensor→body. Legacy mapping `xyz<-x,z,y` có nghĩa Xxe←Xsensor, Yxe←Zsensor, Zxe←Ysensor; đây là tùy chọn tương thích, ưu tiên ma trận quay Euler vật lý. Không dùng `identity,yaw=-1` thay cho một phép quay 3D thật nếu chưa xác nhận cách lắp.
2. Chọn **Nhập CSV IMU 14 cột**. Panel hiện nguồn, số mẫu, span, rate, đơn vị, sáu giá trị và đồ thị. Trước START là preview, không phải telemetry trực tiếp từ cảm biến.
3. Offset hỗ trợ tới 0,001ms (1µs); chỉ cộng offset trong RAM. Chế độ mặc định rebase mỗi nguồn về dòng đầu: đó là **ghép thử nghiệm**, không tự thành đồng bộ. Log thật cùng clock dùng giữ mốc (`Log đồng bộ`) và offset có bằng chứng; checkbox chỉ là khai báo, GUI vẫn ghi chưa xác minh. Đổi mount/profile/clock mode sau import phải nhập lại; offset áp dụng khi START. Offset làm timestamp âm hoặc trùng ms giao thức bị từ chối, không âm thầm cắt mẫu.
4. Radar importer/schema/khung rỗng giữ nguyên. Phiên có cả radar và IMU chờ cả hai EOF mới STOP; stream hết sớm không được bịa thêm mẫu, có thể báo stale. IMU riêng ghép radar tổng hợp thì EGO chỉ là hoạt ảnh minh họa, không tích phân IMU thực thành quỹ đạo.
5. Bấm **Xuất CSV IMU từ mô hình phiên này** để xuất những mẫu tổng hợp thực sự đã tạo trong phiên, lượng tử RAW theo profile và sidecar ghi cùng session clock với TX radar. CSV xuất này theo nhịp mô hình hiện chạy (mặc định100Hz), không tự nâng thành200Hz. Capture tối đa200000 mẫu; nếu thiếu/saturation/tràn thì từ chối export, không báo thành công thiếu dữ liệu. Đây là export tổng hợp, không biến replay thu thực thành dữ liệu giả lập.

Đồ thị gia tốc/gyro có trục thời gian giây; chỉ preview tối đa2000 mẫu gần thời điểm hiện tại, giảm điểm theo pixel để vẽ. Đó là giảm điểm **hiển thị**, không bỏ mẫu gửi. IMU đang PAUSE giữ thời điểm đã đọc. Đèn trái/phải vẫn lấy từ RESULT hợp lệ của ESP32; không lấy scenario/preview để tô màu.

## Băng thông và replay trên bo

Wire giữ sáu giá trị vật lý + timestamp/envelope; không gửi RAW/nhiệt độ/14 cột. Preflight dùng estimate bảo thủ `speed×(IMU_rate×330 + 2×1000/radar_period_ms×(210+max_targets×170))`, trần80% baud/10. Với radar65ms/max4 và IMU200Hz:1× estimate93384,6B/s >73728 nên **từ chối**, 0,5× estimate46692,3B/s đạt. CSV vẫn200Hz; phát chậm wall-time không phải bỏ mẫu/resampling. Không có resampling với anti-alias mới; không tăng baud để ép chạy.

GUI dùng tick16ms và grid mô phỏng5ms khi replay IMU: timestamp µs vẫn giữ nguyên, nhưng Serial/OS có jitter và có thể gửi nhiều mẫu due trong một tick. Không cam kết phát hard realtime µs. Không tự làm tròn rồi đẩy timestamp ms về tương lai; nếu ms không thể tăng nghiêm ngặt, preflight từ chối.

Bo phải chạy `sensor_simulation`, COM rảnh, **921600baud**, START được ACK đúng session/seq trước khi gửi mẫu. Không suy ra binary đã chạy chỉ vì build thành công. [Bộ nạp tay](../../docs/testing/manual-firmware-2026-11-18.md) vẫn dùng khi cần; nhiệm vụ này không nạp.

Lệnh đã chạy từ thư mục simulator (COM5 được phát hiện, hãy quét lại lần sau):

```powershell
.venv/Scripts/python.exe -X utf8 verify_imu_csv.py --port COM5 --radar ../samples/generated/left_approach_45s_dual.csv --imu ../samples/imu14-v1/lane_left-45s-200hz.csv --speed 0.5 --out ../../.local/imu14-board-2026-11-18
.venv/Scripts/python.exe -m pytest -q
```

CLI replay kết thúc tự đóng Serial và lưu JSONL TX/RX, summary, trace. Lượt này đủ9000mẫu, không parser error; cặp ghép **không xuất HIGH** trong quan sát, không sửa luật để ép cảnh báo. Xem báo cáo để biết p50/p95/max và giới hạn; không dùng kết quả đó làm nghiệm thu phần cứng cảm biến thật.
