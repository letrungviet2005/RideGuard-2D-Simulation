# Hướng dẫn chạy RideGuard Sensor Lab

Tài liệu này hướng dẫn ba việc:

1. [Chạy app mô phỏng trên máy tính](#1-chạy-app-mô-phỏng-trên-máy-tính)
2. [Nạp firmware `sensor_simulation` lên ESP32](#2-nạp-firmware-sensor_simulation-lên-esp32)
3. [Hiểu các trường giả lập trong app và tác dụng của chúng](#3-các-trường-giả-lập-trong-app-và-tác-dụng)

> **Cần đo thật radar + IMU?** Xem [THU-LOG-THAT.md](THU-LOG-THAT.md) —
> hướng dẫn nạp firmware logger, thu đồng bộ LD2417 + BMI160, đối chiếu
> với mô phỏng cho kịch bản đi thẳng / rẽ trái / rẽ phải / rung đường.

> **Nguyên tắc quan trọng:** Đèn cảnh báo trên giao diện **luôn lấy từ `RESULT` thật của ESP32**, không bao giờ lấy từ kỳ vọng của kịch bản. Muốn biết firmware nghĩ gì thì nhìn panel **KẾT QUẢ THỰC TỪ ESP32**. Khi chưa có phản hồi hợp lệ, panel hiện *"Chưa có phản hồi ESP32 hợp lệ"* chứ không tự suy diễn `NONE`.

---

## 1. Chạy app mô phỏng trên máy tính

### 1.1. Yêu cầu

| Hạng mục | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10/11 (hướng dẫn dùng PowerShell) |
| Python | 3.11 trở lên, có sẵn lệnh `python` |
| ESP32 | Board ESP32-D0WD-V3 (4 MB flash) + cầu USB-UART CP210x |
| Cổng Serial | Board dùng **921600 baud, 8N1** (khớp firmware) |

Không cần backend, PostgreSQL, Docker hay Android cho toàn bộ quy trình này.

### 1.2. Cài đặt lần đầu

Mở PowerShell tại gốc repository:

```powershell
# 1. Tạo môi trường Python riêng cho simulator
python -m venv tools/rideguard-simulator/.venv

# 2. Cài thư viện
& tools/rideguard-simulator/.venv/Scripts/python.exe -m pip install -r tools/rideguard-simulator/requirements.txt
```

Chỉ cần làm hai bước này **một lần duy nhất**. Các lần sau chạy app bằng lệnh tắt ở mục 1.3.

### 1.3. Mở app

Có hai cách tương đương:

```powershell
# Cách A — dùng script tiện lợi (giữ nguyên thư mục làm việc cho bạn)
.\scripts\run-simulator.ps1
```

```powershell
# Cách B — chạy trực tiếp
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 -m rideguard_sim
Pop-Location
```

Cờ `-X utf8` là bắt buộc để tiếng Việt trong log và cửa sổ không bị lỗi encoding trên Windows.

### 1.4. Thao tác với app

**Bước 1 — Quét cổng.** Bấm **Quét cổng**, chọn đúng board (ví dụ `COM5 · CP210x USB UART`). Nếu không thấy cổng, kiểm tra cáp và driver CP210x đã cài.

**Bước 2 — Kết nối.** Giữ baud là **921600**, bấm **Kết nối**.

> ⚠️ **Đóng mọi Serial Monitor** (Arduino Serial Monitor, PlatformIO Monitor, PuTTY…) trước khi kết nối. Chỉ một chương trình được giữ cổng tại một thời điểm.
>
> ⚠️ Khi mới mở cổng, panel sẽ báo *"Đang chờ board khởi động xong (dọn nhiễu boot ROM)…"* trong khoảng 1–2 giây. Đây là hành vi bình thường, **đừng ngắt cáp**. ESP32 sau mỗi lần reset đẩy ra ~1780 byte nhiễu boot ROM; nếu gửi dữ liệu ngay lúc đó, firmware sẽ parse hỏng và khoá phiên vĩnh viễn. App đã tự chờ và dọn nhiễu.

**Bước 3 — Chọn nguồn dữ liệu và kịch bản.**

- *Nguồn tổng hợp* (mặc định): app tự sinh xe, radar và IMU.
- *Radar ghi thực*: bấm **Nhập CSV / log D/E**, chọn file, và chọn radar thuộc (LEFT/RIGHT) **trước** khi nhập.
- *IMU ghi thực (BMI160)*: bấm **Nhập log IMU (BMI160)** để dùng log IMU thật thay cho IMU giả lập. Xem mục 1.6.

**Bước 4 — Chạy.** Chọn kịch bản, bấm **Bắt đầu / Reset phiên**. Theo dõi hai panel bên phải:

- *DỮ LIỆU ĐANG GỬI* — bản tin app vừa gửi đi (để debug giao thức).
- *KẾT QUẢ THỰC TỪ ESP32* — phản hồi thật của board, kèm `reason_code`.

**Bước 5 — Điều khiển.** Nút **Tạm dừng / Chạy tiếp** và **Dừng phiên** đều chờ **ACK** từ board. Nếu 3 giây không có ACK, app đóng kết nối chứ không giả ACK.

**Bước 6 — Chỉnh xe.** Ở nhóm *3 · XE / QUỸ ĐẠO*, chọn `EGO` hoặc một target, bấm **Đọc xe đang chọn** để nạp giá trị hiện tại, sửa, rồi bấm **Áp dụng khi tạm dừng** (chỉ chạy được khi đang tạm dừng). Có thể kéo xe trực tiếp trên màn hình khi tạm dừng.

**Bước 7 — Xuất log.** Bấm **Xuất log TX/RX + cấu hình** để lưu file `.jsonl` làm bằng chứng. Khi phát lại radar ghi thực, file `.source.txt` chứa raw recording cũng được ghi kèm. Log tự động nằm ở `.local/simulator-logs/` và **không được commit**.

### 1.5. Chạy kiểm thử

Đóng app trước khi chạy các lệnh sau (vì chúng chiếm cùng cổng COM).

```powershell
Push-Location tools/rideguard-simulator

# Unit test — dùng fake transport, KHÔNG cần board
& .venv/Scripts/python.exe -X utf8 -m pytest -q

# Kiểm thử phần cứng — cần ESP32 thật, assert kết quả RESULT từ board
& .venv/Scripts/python.exe -X utf8 hardware_acceptance.py --port COM5
& .venv/Scripts/python.exe -X utf8 hardware_scenarios.py --port COM5

# Ảnh giao diện (Qt offscreen, không mở cửa sổ)
& .venv/Scripts/python.exe -X utf8 capture_ui.py --port COM5 --out ../../.local/ui

Pop-Location
```

### 1.6. Nạp log IMU BMI160 (ghi thực)

Bấm **Nhập log IMU (BMI160)** trong nhóm *2 · NGUỒN / ĐIỀU KHIỂN*. File CSV phải có các cột:

```
timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C
```

App làm gì với từng cột:

| Cột | Xử lý |
|---|---|
| `timestamp_us` | đổi sang **ms tương đối** so với mẫu đầu; mẫu trùng ms được lượng tử tiếp để timestamp tăng nghiêm ngặt |
| `ax_g,ay_g,az_g` | **× 9.80665** → m/s². Giữ trọng lực: đứng yên ngang là `(0, 0, 9.80665)` |
| `gx_dps,gy_dps,gz_dps` | **giữ nguyên**, đã là °/s |
| `ax_raw…gz_raw` | **không dùng** |
| `temperature_C` | chỉ để tham khảo, không ảnh hưởng cảnh báo |

**Vì sao không dùng cột RAW?** RAW là số LSB. Muốn ra m/s² hoặc °/s thì phải có full-scale và sensitivity thật của BMI160 trên board của bạn, cộng thêm calibration. Chưa có thì app **không đoán** — nó bỏ qua cột RAW và ghi rõ trong panel IMU.

**Chọn đúng chiều lắp.** Combo **Gắn BMI160**:

| Lựa chọn | Ý nghĩa |
|---|---|
| `identity` | cảm biến X/Y/Z đã là X tiến / Y trái / Z lên của xe |
| `xyz<-x,z,y` | board nằm ngang: X xe ← Z cảm biến, Y xe ← Z cảm biến, Z xe ← Y cảm biến |
| `xyz<-x,-y,z` | đảo chiều trục Y của cảm biến |
| `identity,yaw=-1` | cảm biến úp ngược, đảo chiều yaw |

Chọn sai thì cảnh báo bị **lật trái↔phải**. `gz_dps > 0` chỉ có nghĩa "yaw sang trái" **sau khi** đã xác nhận chiều lắp; gyro đo vận tốc góc, không phải góc ghi đông.

**Hai log dùng hai đồng hồ khác nhau.** Ô **Lệch đồng hồ IMU** (ms) dịch đồng hồ IMU sang đồng hồ radar; mặc định 0. Không tự coi hai mốc bằng nhau chỉ vì cùng đơn vị ms — bạn phải canh bằng tay.

Kiểm chứng bằng script (cần board thật):

```powershell
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 hardware_imu_import.py --port COM5 `
    --radar ../samples/generated/left_approach_multi_45s_dual.csv `
    --imu ../samples/generated/imu_bmi160_45s.csv --mount identity
Pop-Location
```

Kết quả kỳ vọng: `filtered_yaw_dps` khớp `gz_dps` trong file, `motion=LEFT`, có `TURN_TOWARD_NEAR_TARGET` (HIGH), `parser_errors=0`, không `IMU_STALE`.

---

## 2. Nạp firmware `sensor_simulation` lên ESP32

### 2.1. Firmware nào dùng cho việc gì?

Repository có 4 PlatformIO environment:

| Environment | Dùng để | Ghi đè firmware board? |
|---|---|---|
| `sensor_simulation` | Firmware cho **app mô phỏng** này — nhận JSON qua Serial, tính cảnh báo, trả `RESULT` | ✅ Có |
| `ble_navigation_demo` | Demo BLE navigation (UUID, ACK, TTL 10 s) — **tính năng có sẵn, không phá** | ✅ Có |
| `ld2417` | Firmware radar thật LD2417 | ✅ Có |
| `ld2451` | Firmware radar thật LD2451 | ✅ Có |

App mô phỏng **cần** `sensor_simulation`. Nếu bạn nạp nhầm env khác, app sẽ không nhận được `RESULT` nào.

### 2.2. Chuẩn bị đường dẫn

Đường dẫn workspace hiện tại chứa khoảng trắng và dấu ngoặc, gây lỗi khi build. Dùng ổ tạm:

```powershell
# Kiểm tra ổ R: đã có ai dùng chưa — nếu có, chọn ổ khác
subst

# Tạo mapping
subst R: "$PWD"

# Build firmware (không nạp vội, xem lỗi build trước)
& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" -m platformio run -d R:/firmware -e sensor_simulation
```

> Nếu `pio.exe` bị Windows Application Control chặn, dùng `python -m platformio` như trên. Nếu build báo thiếu `intelhex`, cài: `& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" -m pip install intelhex`.

### 2.3. Nạp firmware — vì sao cần nhấn tay BOOT

Cổng CP210x trên board này **không** kéo được chân BOOT (GPIO0) bằng tín hiệu RTS. Vì vậy `platformio run -t upload` thường báo:

```
Wrong boot mode detected (0x13)
```

Nguyên nhân: ESP32-D0WD-V3 cần GPIO0 kéo xuống mức thấp **đúng lúc reset** để vào bootloader. Script `flash_sensor_simulation.py` giải quyết việc này bằng cách nhấn cứng.

### 2.4. Các bước nạp

```powershell
# Bước 1: Build (nếu chưa build ở mục 2.2 thì build lại)
& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" -m platformio run -d R:/firmware -e sensor_simulation

# Bước 2: Mở script nạp (để chạy nền, chờ tín hiệu)
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 flash_sensor_simulation.py --port COM5
```

Script sẽ in ra:

```
HOLD BOOT, then create the trigger file: ...\tools\rideguard-simulator\.flash-trigger
waiting for trigger...
```

**Bước 3 — Giữ nút BOOT** (nút đẩy xuống) **và tạo file trigger** ở cửa sổ PowerShell khác.

File trigger nằm **cùng thư mục với script**, tức là `tools/rideguard-simulator/.flash-trigger`. Dùng đường dẫn tuyệt đối để không nhầm:

```powershell
New-Item -ItemType File -Path "$PWD\tools\rideguard-simulator\.flash-trigger" -Force | Out-Null
```

> Nếu bạn đang đứng ở thư mục `tools/rideguard-simulator` thì dùng `New-Item -ItemType File -Path ".flash-trigger" -Force`. Script tự xoá file này sau khi nhận, nên không cần dọn thủ công.

**Bước 4 — Đợi script báo:**

```
chip reset; BOOT can be released now
  rom bytes: 1780  head: b'\x80\x80\x80...'
  boot:0x1f ...
```

→ **Thả nút BOOT** ngay khi thấy dòng này.

**Bước 5 — Đợi kết quả nạp:**

```
Hash of data verified
FLASH OK - press EN once to run the new firmware.
```

→ **Nhấn nút EN một lần** để chạy firmware mới.

**Bước 6 — Giải phóng ổ tạm:**

```powershell
subst R: /D
```

Chỉ gỡ mapping nếu chính bạn tạo. Không chạy song song nhiều uploader hay Serial Monitor.

### 2.5. Nạp bằng cách thủ công (không dùng script)

Nếu muốn tự làm qua esptool:

```powershell
# Giữ BOOT, nhấn EN, thả EN, giữ BOOT — rồi chạy:
& "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" `
  --chip esp32 --port COM5 --baud 115200 `
  --before no_reset --after hard_reset `
  write_flash 0x10000 "R:/firmware/.pio/build/sensor_simulation/firmware.bin"
```

Dùng **115200 baud** — đây là tốc độ gốc của ROM loader, đồng bộ ổn định hơn 921600.

### 2.6. Trả lại firmware BLE standalone

Sau khi dùng xong app mô phỏng, để quay lại demo BLE:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" -m platformio run -d R:/firmware -e ble_navigation_demo -t upload --upload-port COM5
subst R: /D
```

Backup LED ban đầu vẫn ở `.local/esp32-com5-before-navigation-2026-11-17.bin` — **không ghi đè file này**.

### 2.7. Bảng lỗi thường gặp khi nạp

| Lỗi | Nguyên nhân | Cách xử lý |
|---|---|---|
| `Wrong boot mode detected (0x13)` | GPIO0 không được giữ khi reset | Dùng `flash_sensor_simulation.py` (giữ BOOT + tạo file trigger) |
| `Invalid head of packet (0x6D)` | Nhiễu serial, chip không vào bootloader | Thả BOOT đúng lúc, thử lại, đảm bảo không có chương trình nào giữ cổng |
| `A fatal error occurred: Failed to connect` | Sai cổng COM, hoặc cáp lỗi | Kiểm tra **Quét cổng** trong app xem cổng nào đúng |
| `ModuleNotFoundError: intelhex` | Thiếu package trong PlatformIO | `pip install intelhex` vào venv của PlatformIO |
| Board không trả `RESULT` sau khi nạp | Đang chạy env khác, hoặc chưa nhấn EN | Nhấn EN; build lại đúng env `sensor_simulation` |

---

## 3. Các trường giả lập trong app và tác dụng

Có 4 nhóm trường. **Mọi thay đổi đều chỉ áp dụng cho phiên mới** — phải bấm **Bắt đầu / Reset phiên** để có hiệu lực.

### 3.1. Nhóm 3 · XE / QUỸ ĐẠO

| Trường | Ý nghĩa | Tác dụng thực tế |
|---|---|---|
| `EGO` / `ID31` | Chọn xe muốn chỉnh | EGO là xe của bạn (chạy app); ID31 là xe khác trong kịch bản |
| **Dọc X (m)** | Khoảng cách dọc trục tiến | X âm = phía sau. Dùng để dời xe ra xa gần |
| **Ngang Y trái (m)** | Lệch ngang bên trái | Y dương = lệch trái, Y âm = lệch phải |
| **Tốc độ (km/h)** | Tốc độ xe đang chọn | Quyết định `v_kmh` mà radar đo được, và quyết định xe có đuổi kịp hay không |
| **Hướng (độ)** | Góc hướng xe | 0° = thẳng, dương = rẽ trái. Tác động trực tiếp lên góc `angle_deg` radar đo được |
| **Thời gian chuyển làn (s)** | Thời lượng quy trình chuyển làn | Càng ngắn → càng mạnh. Đây là tham số **quan trọng nhất** để tạo cảnh báo HIGH: yaw lớn ở đầu/cuối quy trình |
| **Đọc xe đang chọn** | Nạp giá trị hiện tại vào ô nhập | Xem trạng thái thật của xe trước khi sửa |
| **Áp dụng khi tạm dừng** | Ghi giá trị mới vào xe | Chỉ hoạt động khi đang **tạm dừng** và dùng nguồn tổng hợp |
| **Chuyển làn trái +3.5 m / phải −3.5 m** | Lệnh chuyển làn bằng đường cong quintic | Tạo ra đổi hướng mà radar/IMU sẽ ghi nhận |
| **Đi thẳng theo hướng đường** | Bỏ chuyển làn, quay về 0° | Dùng để kết thúc một chuỗi chuyển làn |

> **Hiện tượng đáng chú ý:** đường cong quintic có vận tốc ngang bằng 0 ở **cả hai đầu**. Nghĩa là xe **lái ngược** khi kết thúc chuyển làn. Vì vậy trong kịch bản "Hai bên có xe · ego sang trái", HIGH xuất hiện ở **phía phải** trong lúc xe quay trả, còn phía trái chỉ ở mức MONITOR. Đây là hành vi vật lý đúng, không phải lỗi.

### 3.2. Nhóm 4 · THAM SỐ (chỉ áp dụng cho phiên mới)

Đây là bộ tham số mô phỏng cảm biến và ngưỡng cảnh báo. Tách thành hai nhóm con:

#### a) Thông số radar giả lập — quyết định app *gửi gì* đi

| Trường | Mặc định | Tác dụng |
|---|---|---|
| **Tầm mô phỏng (m)** | 40 | Bán kính phát hiện. Xe ngoài tầm **không** xuất hiện trong khung radar → phía đó hiện `NONE` |
| **Nửa góc quét (độ)** | 60 | Nửa FOV tính từ trục chính. Góc âm = bên trái, góc dương = bên phải (theo mount mặc định). Target ngoài FOV cũng biến mất |
| **Chu kỳ radar (ms)** | 65 | Nhịp gửi khung radar. Giảm → cập nhật nhanh hơn nhưng **tốn băng thông Serial** |
| **Giới hạn mục tiêu** | 4 | Số target tối đa trong một khung (giới hạn cứng của giao thức). Khung bị cắt bỏ theo khoảng cách gần nhất |
| **Nhiễu Y sigma (m)** | 0 | Nhiễu Gauss cộng vào khoảng cách đo. 0 = lý tưởng. Mô phỏng sai số đo của radar thật |
| **Xác suất mất khung** | 0 | Xác suất **không gửi khung** (không phải khung rỗng). Dùng để kiểm tra firmware có phát hiện `RADAR_STALE` hay không |
| **P(associated=0)** | 0 | Xác suất target bị đánh dấu chưa gắn kết. Target vẫn giữ track — firmware **không** được xóa target vì lý do này |

#### b) Tham số ngưỡng cảnh báo — quyết định firmware *báo gì*

| Trường | Mặc định | Tác dụng |
|---|---|---|
| **Ngưỡng yaw (độ/s)** | 2 | Tốc độ quay đã lọc tối thiểu để coi là "đang rẽ". Tăng lên → ít HIGH hơn; giảm → dễ HIGH hơn |
| **Xác nhận chuyển hướng (ms)** | 200 | Thời gian yaw phải vượt ngưỡng liên tục trước khi tín hiệu được chấp nhận. Lọc nhiễu gyro |
| **Vùng gần (m)** | 18 | Khoảng cách mà target được coi là **nguy hiểm gần**. HIGH chỉ xảy ra khi target ở vùng này **và** xe đang quay về phía target |

> **Cơ chế HIGH của firmware:** `HIGH / TURN_TOWARD_NEAR_TARGET` = có target ở vùng gần (`near_m`) **VÀ** `motion` của xe đang hướng về phía target đó. Đèn được giữ tối đa 350 ms kể từ lúc phát hiện.
>
> Ba mức khác: `NONE / EMPTY_VALID_FRAMES` (cảm biến ổn, không có target bên đó) · `MONITOR / TARGET_IN_ZONE` (có target hợp lệ) · `UNKNOWN` (chưa có phiên / đã pause / dữ liệu quá cũ / input lỗi).

#### c) Nhịp và băng thông — hai trường liên quan đến tốc độ

| Trường | Mặc định | Tác dụng |
|---|---|---|
| **Chu kỳ IMU (ms)** | 10 | Nhịp gửi mẫu IMU (100 Hz). Đây là **nguồn tiêu thụ băng thông lớn nhất** vì mỗi mẫu IMU dài ~330 byte |
| **Tốc độ thời gian** | 1× | Tốc độ phát lại mô phỏng (0.5× / 1× / 2×). Ảnh hưởng mạnh đến băng thông |

**Bảng cấu hình tốc độ hợp lệ** (ngân sách = 80% × baud/10):

| Tốc độ | Chu kỳ radar | Chu kỳ IMU | Kết quả |
|---|---|---|---|
| 0.5× | 65 ms | 10 ms | ✅ thoải mái |
| 1× | 65 ms | 10 ms | ✅ mặc định |
| 2× | 65 ms | 50 ms | ✅ cần tăng chu kỳ IMU |
| 2× | 65 ms | 10 ms | ❌ **vượt ngân sách** → app từ chối ngay khi bấm Start |

Khi cấu hình vượt ngân sách, app **báo lỗi và từ chối bắt đầu** thay vì gửi rồi để board phát hiện lỗi `RX_DESYNC`.

### 3.3. Nhóm 1 · KẾT NỐI ESP32

| Trường | Tác dụng |
|---|---|
| **Cổng** | Cổng Serial của board (COM5 với CP210x). Bấm **Quét cổng** để làm mới danh sách |
| **Baud** | Phải khớp firmware. Firmware này cố định **921600**. Đổi sang 460800/115200 chỉ dùng được nếu bạn build lại firmware tương ứng |

### 3.4. Nhóm 2 · NGUỒN / ĐIỀU KHIỂN

| Trường | Tác dụng |
|---|---|
| **Combo kịch bản** | Chọn 1 trong 10 kịch bản. Xem bảng bên dưới |
| **CSV thuộc radar** | Chọn LEFT/RIGHT cho radar ghi thực. **Phải chọn trước** khi bấm Nhập CSV |
| **Nhập CSV / log D/E** | Nạp radar đã ghi. Hỗ trợ CSV nhóm theo ms/frame/radar và log D/E của Hi-Link |
| **Về mô phỏng tổng hợp** | Bỏ chế độ replay, quay lại dữ liệu do app sinh |
| **Replay + IMU giả lập** | Bật IMU giả khi phát lại radar ghi thực. **Chưa đồng bộ** với dữ liệu CSV |
| **Gắn BMI160** | Cách gắn cảm biến: trục cảm biến → trục xe (X tiến, Y trái, Z lên). Xem mục 1.6 |
| **Lệch đồng hồ IMU** | Dịch đồng hồ log IMU sang đồng hồ log radar, tính bằng ms. Mặc định 0 |
| **Nhập log IMU (BMI160)** | Nạp log IMU thật để thay cho IMU giả lập. Xem mục 1.6 |
| **Bỏ log IMU** | Quay lại IMU giả lập |
| **Tốc độ thời gian** | 0.5× / 1× / 2× — xem bảng ngân sách ở mục 3.2c |
| **Bắt đầu / Reset phiên** | Mở phiên mới, xóa toàn bộ trạng thái cũ trên board, gửi cấu hình + tham số mới |
| **Tạm dừng / Chạy tiếp** | PAUSE / RESUME, đều chờ ACK. Khi PAUSED kết quả là `UNKNOWN / PAUSED` |

#### Danh sách 10 kịch bản

| # | Tên | Mục đích kiểm tra |
|---|---|---|
| 0 | Xe trái tiến gần → chuyển làn trái | Luồng chuẩn: MONITOR rồi HIGH bên trái |
| 1 | Đi thằng · đường trống | Chứng minh `NONE` khi không có target |
| 2 | Xe phía sau trái tiến gần | Target đi vào vùng phát hiện từ phía sau |
| 3 | Xe phía sau phải tiến gần | Đối chiếu với #2 (target về phía phải) |
| 4 | Xe trái · ego chuyển làn phải | Ego rẽ **về phía đối diện** với target |
| 5 | Hai bên có xe · ego sang trái | Có target cả hai bên — kiểm tra việc xác định bên cảnh báo |
| 6 | Mục tiêu vào / ra FOV | Target ra khỏi góc quét → phía đó về `NONE` |
| 7 | Rung hướng ngắn · kiểm tra lọc | Yaw dao động nhỏ **không** được báo HIGH (nhỏ hơn ngưỡng) |
| 8 | Mất radar trái trong cảnh báo | Từ giây 3.5 ngừng gửi radar trái → firmware báo `RADAR_STALE` |
| 9 | Mất IMU trong cảnh báo | Từ giây 3.5 ngừng gửi IMU → firmware báo `IMU_STALE` |

> Kịch bản **không** gửi nhãn LEFT/HIGH cho ESP32. App chỉ gửi dữ liệu cảm biến thô; firmware tự suy luận.

### 3.5. Các trường trong panel bên phải

| Panel | Nội dung |
|---|---|
| **DỮ LIỆU ĐANG GỬI** | JSON bản tin app vừa gửi. Dùng để debug giao thức |
| **KẾT QUẢ THỰC TỪ ESP32** | JSON `RESULT` thật từ board: `left`/`right` với `level`, `reason_code`, `track_id`, `radar_id`; kèm `motion`, `filtered_yaw_dps`, độ tuổi từng cảm biến |
| **Log sự kiện** | 25 dòng gần nhất: ACK, ERROR, cảnh báo ngân sách |

---

## 4. Giới hạn cần biết trước khi dùng kết quả

- Đây là **mô hình chức năng hình học**, không phải thuật toán tracking của nhà sản xuất.
- **Chưa có** radar/IMU/HUD/rung thật trong phép thử này. Không xác nhận được khả năng lắp gương, độ chính xác góc, độ nghiêng, nhiễu đa đường, che khuất, radar association thật, TTC hay motion estimator từ dữ liệu đo.
- Vị trí và góc mount radar là **giả định phần mềm**, chưa đo trên gương thật.
- Dữ liệu CSV mẫu chỉ có 7 khung trong 378 ms — đây là trích đoạn, không phải dataset đầy đủ có nhãn.
- Tốc độ `v_kmh` trong giao thức Hi-Link là **unsigned**, **không được** coi là tốc độ tiếp cận có dấu. Bộ luật hiện không ước lượng TTC.
- Firmware `sensor_simulation` **không** điều khiển HUD, rung hay GPIO cảm biến.

## 5. Tài liệu liên quan

| Tài liệu | Nội dung |
|---|---|
| [`contracts/sensor-simulation.md`](../../contracts/sensor-simulation.md) | Đặc tả giao thức đầy đủ (envelope, miền giá trị, ngưỡng) |
| [`docs/testing/simulator-2026-11-17.md`](../../docs/testing/simulator-2026-11-17.md) | Báo cáo kiểm chứng 13/13 trên phần cứng thật |
| [`docs/testing/simulator-result-timing-2026-11-17.md`](../../docs/testing/simulator-result-timing-2026-11-17.md) | Báo cáo đo nhịp RESULT + importer IMU BMI160 (lượt tối) |
| [`README.md`](README.md) | Mô tả tổng quan + runbook ngắn |
| [`contracts/ble-protocol.md`](../../contracts/ble-protocol.md) | Giao thức BLE navigation (giữ nguyên, không bị ảnh hưởng) |
