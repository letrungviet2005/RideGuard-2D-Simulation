# RideGuard Sensor Lab — mô phỏng desktop 2D

**Bố cục thư mục:** `rideguard_sim/` là ứng dụng, `tests/` là test tự động, [scripts/](scripts/README.md) gồm CLI theo nhóm. Hướng dẫn chuyên đề và báo cáo tập trung tại [docs/simulator](../../docs/simulator/README.md). Lệnh mở GUI giữ nguyên; script tiện ích dùng đường dẫn mới `scripts/<nhóm>/<tên>.py`.

Ứng dụng PySide6 vẽ đường/xe, sinh radar + IMU hoặc phát lại CSV/dataset. Mặc định **Bộ luật · Máy tính** chạy cùng model AI mà không cần bo. Trong tab Kết nối ESP32 có thể chọn **ESP32 thật – qua USB/Serial**: chỉ dùng RESULT thật, không fallback PC. Có vàng theo khoảng cách và thử gyro Z bằng tay cho nguồn tổng hợp. [Báo cáo mới, lệnh test, gói nạp tay và giới hạn](../../docs/simulator/reports/pc-rules-2026-11-19.md); firmware mới chỉ build, chưa nạp/nghiệm thu trên bo.

Đọc [giao thức](../../contracts/sensor-simulation.md), [báo cáo kiểm chứng](../../docs/simulator/reports/simulator-2026-11-17.md) trước khi dùng kết quả. Không phải mô hình sóng radar hoặc công cụ chứng minh an toàn giao thông.

> **Mới:** xem [HUONG-DAN.md](../HUONG-DAN.md) để có hướng dẫn đầy đủ từng bước — cài app, nạp firmware ESP32 (kèm xử lý nút BOOT), và ý nghĩa/tác dụng của từng trường giả lập trong giao diện.

## Chạy từ gốc repository — PowerShell

Chỉ cần Python 3.11+, không dùng backend, PostgreSQL, Docker hoặc Android:

```powershell
python -m venv tools/rideguard-simulator/.venv
& tools/rideguard-simulator/.venv/Scripts/python.exe -m pip install -r tools/rideguard-simulator/requirements.txt
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 -m rideguard_sim
Pop-Location
```

Chỉ tạo venv/cài dependency ở lần đầu. Các lần sau dùng ba dòng Push-Location/python/Pop-Location. Có thể chạy `scripts/run-simulator.ps1` sau khi đã chuẩn bị venv.

1. Đóng Serial Monitor đang giữ cổng. Bấm **Quét cổng**, chọn đúng bo và baud **921600**, rồi Kết nối.
2. Chọn **Xe trái tiến gần → chuyển làn trái**, Bắt đầu/Reset. Mục tiêu ID31 đi 43.2 km/h, ego 36 km/h; ego bắt đầu chuyển làn sau 2 giây. Theo dõi MONITOR rồi HIGH bên trái và reason từ bo.
3. Pause/Resume/Stop chờ ACK. Reset tạo session mới. Không có ACK trong 3 giây sẽ đóng kết nối.
4. Cuộn panel trái xuống để chỉnh xe/cảm biến. Vị trí/tốc độ/hướng chỉ áp dụng khi pause; có thể kéo xe khi pause. Chọn xe rồi dùng nút chuyển làn trơn 2–12 giây. Cuộn chuột trên đường để zoom.
5. Export log hoặc dùng file tự ghi trong `.local/simulator-logs/`. Các log không được commit.

Ba mức thời gian 0.5×/1×/2× độc lập ngưỡng firmware theo `t_ms`. Cấu hình vượt ngân sách Serial bị từ chối khi Start. **Chu kỳ IMU** (mặc định 10 ms) cũng tính vào ngân sách: ở 2×, IMU 100 Hz là phần chiếm nhiều nhất, nên tăng chu kỳ IMU lên 50 ms giúp 2× vừa 921600 baud. Mặc định 4 targets/65 ms chạy 1×; để 2× ở 921600 dùng radar 65 ms + IMU 50 ms. `stale_ms=260` sát chu kỳ 250 nên thích hợp demo lý tưởng; không dùng dropout ở cấu hình này. Không đổi baud UI khi firmware vẫn dùng 921600.

Khi byte bị rơi trên UART, firmware báo `RX_DESYNC` (khung bắt đầu không phải `{`) và khoá phiên — đây là hành vi an toàn, không phải lỗi cần che. Khi đó phải tạo phiên mới. `ERROR` còn kèm `field=` nếu là giá trị ngoài miền cho phép.

## Kịch bản

Sau khi luồng đầu tiên đạt trên bo, các lựa chọn được mở rộng: đường trống; xe nhanh bên trái; xe nhanh bên phải; xe trái + ego sang phải; hai bên + ego sang trái; vào/ra FOV; xung heading ngắn; mất radar trái; mất IMU trong cảnh báo. Có 10 lựa chọn vì radar loss và IMU loss tách riêng.

Kịch bản không gửi nhãn LEFT/HIGH cho ESP32. Chuyển làn dùng quintic; IMU từ vận tốc/heading thật trong mô hình. Khi quay trả hướng cuối chuyển làn, gyro đổi dấu. Bộ luật yaw thử nghiệm có thể coi phần quay trả là chuyển hướng ngược; cần xem trace, không coi cảnh báo này là quyết định an toàn trên đường.

Vì quintic có vận tốc ngang bằng 0 ở hai đầu, xe **lái ngược** khi kết thúc chuyển làn. Vì vậy kịch bản 5 (hai bên có xe, ego sang trái) có HIGH ở **phải** trong lúc xe quay trả, còn bên trái chỉ MONITOR. Quy tắc HIGH của firmware là "mục tiêu trong vùng gần **và** xe đang quay về phía đó", nên kiểm chứng kiểm tra HIGH đi kèm `motion` tương ứng thay vì cố định một phía.

Thông số range/FOV/period/max targets/noise/dropout/associated và ngưỡng yaw/confirm/near chỉnh cho **phiên mới**. Mount hiện cố định trong `radar.py` và firmware theo contract; không chỉnh riêng một phía. Seed mặc định 17. Có lý tưởng (noise/dropout=0) trước khi thêm nhiễu. `associated=0` giữ track, còn dropout không gửi frame.

## Radar ghi thực và IMU giả lập

Bấm **Dừng phiên**, chờ ACK rồi nhập `samples/radar_recorded_excerpt.csv`, chọn LEFT/RIGHT trước nhập. Dữ liệu mẫu chỉ 7 khung trong 378 ms, ID31, là trích đoạn người dùng cung cấp, không phải dataset đầy đủ/có nhãn. Có thể chọn 0.5× để xem; EOF không tự lặp hoặc phát khung rỗng, target chuyển stale.

- CSV nhóm theo ms/frame/radar; D/E logger nhóm nhiều dòng D đến E, kiểm N. E N=0 là khung rỗng hợp lệ. Bỏ dòng `#stats`. Thiếu E, timestamp giảm/lặp trong cùng radar, quá 4 targets hoặc duplicate ID bị từ chối.
- Không có file `ld2417_esp32_usb.ino` trong repository đã rà; parser D/E theo schema người dùng cung cấp, chưa đối chiếu sketch thật.
- Dùng `ms`, không dùng `pc_time` lặp. Giữ raw recording trong log META và file `.source.txt` khi export.
- `huong=trai/phai` → dir=1/2, **không phải radar_id**. associated=0 không mất target, duration là frame.
- Hi-Link gọi y là tọa độ Y; hiển thị dùng `forward=y`, `lateral=y*tan(angle)`. Dữ liệu CSV gốc `x_m≈y*sin(angle)`, `fwd_m≈y*cos(angle)` được giữ nguyên để đối chiếu, không âm thầm sửa. Chiều góc/mount vẫn cần xác minh bằng bố trí radar thật.
- Không bật IMU: không tạo chuyển động ego và ghi **Chưa có dữ liệu chuyển động xe mình**. Chỉ một radar có dữ liệu nên bo phải UNKNOWN; đó không phải lỗi để sửa thành NONE.
- Nếu chủ động bật checkbox IMU giả lập, hiện **Radar ghi thực + IMU giả lập, chưa đồng bộ đo thực**. Đây không phải dữ liệu IMU đo cùng CSV. Không bù radar còn thiếu bằng frame rỗng giả.

## CSV IMU 14 cột (profile theo bảng người dùng)

[Runbook/schema/lệnh tạo CSV](../../docs/simulator/guides/IMU-CSV.md) là hướng dẫn hiện tại. GUI có nút
**Nhập CSV IMU 14 cột**, chọn `user_table_v1` hoặc compatibility `physical_only`,
mount Euler, offset µs, sáu giá trị và đồ thị, export CSV từ phiên tổng hợp.
Giữ µs gốc; không lượng tử tiến khi trùng ms. `user_table_v1` kiểm đúng14cột,
RAW int16 và scale0,000122g/LSB,0,0175dps/LSB; chưa xác minh chip phần cứng.
`physical_only` cho log cũ, không kiểm hệ số RAW. Không tự coi nhập CSV là đo thật.

Mẫu mới `../samples/imu14-v1/` có bảy kịch bản45giây/200Hz, mỗi file9000mẫu;
`../samples/reference/user-provided-14rows.csv` span64,985ms nguồn USER_PROVIDED.
200Hz+radar mặc định bị từ chối ở1× theo ngân sách bảo thủ; chọn0,5× giữ đủ mẫu.
Không suy ra đồng bộ từ cùng thời lượng. [Báo cáo18/11](../../docs/simulator/reports/imu14-2026-11-18.md)
ghi riêng128 test PC,GUIthật và replay9000mẫu trênESP32 với899RESULT; không có
HIGH trong cặp CSV ghép đó. Chưa nghiệm thu cảm biến thật/chuyển làn ngoài đường.

## Thu đồng bộ radar + IMU thật (LD2417 + BMI160)

Firmware `sensor_logger` đọc **một** LD2417 qua UART1 và **một** BMI160 qua
I2C, đóng dấu mọi bản ghi bằng **cùng một bộ đếm `micros()` của ESP32**. Nhờ
vậy log radar và log IMU là một timeline, không cần hiệu chỉnh đồng hồ.

Hướng dẫn đầy đủ (nối chân, nạp firmware, chuỗi thu 4 kịch bản, cách đọc
kết quả đối chiếu): **[THU-LOG-THAT.md](../../docs/simulator/guides/THU-LOG-THAT.md)**.

Tóm tắt:

```powershell
# 1. Nạp firmware logger (ghi đè firmware app, nạp lại sensor_simulation sau)
& firmware/.venv/Scripts/python.exe -m platformio run -d R:\firmware -e sensor_logger -t upload

# 2. Ghi log (gõ s + Enter để bắt đầu kịch bản, e + Enter để kết thúc)
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 scripts/hardware/capture_hardware.py --port COM5 --out captures
Pop-Location

# 3. Đối chiếu với mô phỏng
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 scripts/hardware/compare_capture.py --capture captures --scenario scenario_00
Pop-Location
```

`scripts/hardware/compare_capture.py` mô phỏng lại **đúng state machine** của firmware (lọc
yaw 80 ms, ngưỡng 2 dps, xác nhận 200 ms) trên log IMU đo được, nên trả lời
được "firmware có tin là xe đang rẽ không?" bằng chính dữ liệu thật.

Khi phát lại trong app, **bắt buộc** bật checkbox *Log đồng bộ (radar + IMU
cùng đồng hồ)* và để *Lệch đồng hồ IMU* = 0 ms. Nếu không, hai file sẽ bị
dời riêng và lệch nhau đúng bằng khoảng cách giữa mẫu đầu tiên của mỗi file.

Board chỉ có **một** LD2417 nên log chỉ ghi được một phía radar; app yêu cầu
cả hai phía còn mới, vì vậy firmware báo `RADAR_STALE` khi phát lại log một
radar. Đây là hành vi đúng, không phải lỗi.

Kiểm thử toàn bộ pipeline khi chưa có board:

```powershell
& .venv/Scripts/python.exe -X utf8 scripts/data/make_synthetic_capture.py --out captures_synthetic
```

## Firmware và khôi phục BLE

Environment `sensor_simulation` tái sử dụng `navigation_service.cpp` từ BLE demo (UUID, parse/ACK, TTL 10 giây). Không điều khiển HUD/rung/GPIO cảm biến. Serial parser bounded, không read timeout chặn BLE. Dùng `huge_app.csv` app 3 MB/no OTA để có dư dung lượng; không erase-all, không xóa backup cũ.

Đường dẫn workspace có khoảng trắng/dấu ngoặc: dùng ổ tạm còn trống (kiểm `subst` trước, không chiếm ổ người khác):

```powershell
subst R: "$PWD"
& R:/firmware/.venv/Scripts/python.exe -m platformio run -d R:/firmware -e sensor_simulation
& tools/rideguard-simulator/.venv/Scripts/python.exe -m serial.tools.list_ports -v
```

**Nạp firmware.** Cổng này dùng cầu USB-UART CP210x **không** kéo được chân BOOT
(GPIO0) bằng RTS, nên `platformio run -t upload` thường báo `Wrong boot mode (0x13)`.
Dùng script kèm theo — nó reset chip **trong lúc bạn giữ nút BOOT** rồi nối esptool
mà không reset lần nữa, ở **115200 baud** (tốc độ gốc của ROM loader, đồng bộ ổn
định hơn 921600):

```powershell
# 1. Build trước
& R:/firmware/.venv/Scripts/python.exe -m platformio run -d R:/firmware -e sensor_simulation
# 2. Giữ nút BOOT, rồi tạo file kích hoạt (script đang chờ ở nền)
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 scripts/firmware/flash_sensor_simulation.py --port COM5
#    -> nhấn Enter/giữ BOOT theo hướng dẫn, đợi "FLASH OK", thả BOOT, bấm EN một lần
Pop-Location
```

Không chạy nhiều uploader/Serial Monitor cùng lúc. Ngày 17/11 đã xác nhận CP210x COM5,
ESP32-D0WD-V3 rev3.1, 4 MB. Không hardcode cổng này trong app.

**Vì sao phải chờ sau khi mở cổng.** ESP32 đẩy khoảng **1780 byte không phải JSON**
(ROM loader handshake) vào UART ngay sau mỗi lần reset. Nếu host gửi ngay, board
parse sai dòng đầu, báo `JSON`, và **khoá vĩnh viễn** phiên đó. `SerialLink` đã tự
dọn nhiễu và chờ ổn định trước khi gửi; giao diện hiện "Đang chờ board khởi động
xong (dọn nhiễu boot ROM)…". Đừng mở Serial Monitor cùng lúc với mô phỏng.

Khôi phục firmware BLE standalone (Serial 115200, phân vùng mặc định của env BLE):

```powershell
& R:/firmware/.venv/Scripts/python.exe -m platformio run -d R:/firmware -e ble_navigation_demo -t upload --upload-port COM5
subst R: /D
```

Chỉ bỏ mapping R nếu chính bạn tạo. Backup LED ban đầu vẫn ở `.local/esp32-com5-before-navigation-2026-11-17.bin`; không ghi đè file này. Source/build BLE là cách quay lại demo BLE; không tự restore toàn bộ flash trong luồng chạy mô phỏng.

## Test và phát lại log

```powershell
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 -m pytest -q
# Đóng kết nối GUI trước các lệnh chiếm COM:
& .venv/Scripts/python.exe -X utf8 scripts/testing/hardware_acceptance.py --port COM5
& .venv/Scripts/python.exe -X utf8 scripts/testing/hardware_scenarios.py --port COM5
& .venv/Scripts/python.exe -X utf8 scripts/hardware/replay_session.py ../../.local/simulator-logs/<session>.jsonl --port COM5
# Ảnh giao diện (Qt offscreen), không cần mở cửa sổ:
& .venv/Scripts/python.exe -X utf8 scripts/diagnostics/capture_ui.py --port COM5 --out ../../.local/ui
Pop-Location
git diff --check
```

`pytest` kiểm mô hình/parser/transport state với fake transport và không chứng minh firmware. Các script hardware mở COM thật và assert RESULT; không có chế độ mock thay thế. `scripts/testing/hardware_scenarios.py` tự chọn chu kỳ radar/IMU vừa ngân sách Serial cho biến thể tốc độ, và **từ chối** cấu hình vượt ngân sách thay vì gửi rồi để board phát hiện lỗi. `scripts/hardware/replay_session.py` chỉ gửi TX/OFFLINE, không phát RX cũ làm phản hồi mới. Mỗi lần replay có session/log RX mới.

## Tổ chức mã và giới hạn

`model.py` chuyển động, `radar.py` hình học/track/noise, `imu.py` đơn vị, `replay.py` nguồn đo, `protocol.py` envelope/budget, `serial_link.py` worker bounded, `session.py` ACK/scheduler/log, `scene.py` vẽ/drag, `ui.py` panel/controls, `scenarios.py` đầu vào. Firmware tách input/state/motion/rules/output, BLE dùng module chung.

Nguồn chính thức: [Hi-Link LD2417](https://www.hlktech.com/Goods-342.html), file **HLK-LD2417 串口协议 V1.0.4**, phần 1, bảng 1-2 và ví dụ, phần 2.4/3.2. Xác nhận y-axis, km/h×100, angle signed degrees, ID byte, status bit0/high-speed, bit1/associated, high-byte duration frames. Ví dụ N=3 **không chứng minh thiết bị chỉ hỗ trợ 3 targets**. Giới hạn 4, ngưỡng high-speed 30 km/h, mount, tracking/noise, tầm mặc định 40 m là cấu hình mô phỏng. Chu kỳ 65 ms là cấu hình theo yêu cầu; chưa đo nhịp trên radar thật.

Chưa có radar/IMU/HUD/rung thật trong phép thử này, chưa xác nhận khả năng lắp gương hoặc độ chính xác nguy cơ. Góc/độ nghiêng, nhiễu đa đường, che khuất, radar association thật, TTC và motion estimator dùng dữ liệu đo là việc tiếp theo. Phần backend/mobile không thay đổi trong tác vụ này.
