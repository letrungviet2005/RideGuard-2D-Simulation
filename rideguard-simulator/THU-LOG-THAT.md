# Thu đồng bộ radar LD2417 + IMU BMI160 thật

**Cập nhật sau lượt nạp tay 18/11:** bo COM5 đã ACK giao thức simulator ở 921600
trong [lượt kiểm chứng CSV IMU mới](../../docs/testing/imu14-2026-11-18.md).
Mô tả logger 460800 bên dưới là trạng thái quan sát trước đó. Chưa kiểm hash
binary trên bo; không coi ACK simulator là kiểm chứng logger/cảm biến thật.
Schema/profile mới và cách replay: [IMU-CSV.md](IMU-CSV.md).

**Trạng thái 18/11/2026:** bộ thu/convert đã có test phần mềm, nhưng chưa có
bản thu đồng bộ cảm biến thật được nghiệm thu. Bo hiện được nhận diện COM5,
banner `LD2417 USB logger san sang` ở 460800 baud; chưa xác minh logger này
trùng source `sensor_logger` dưới đây. Backup toàn flash hiện chưa đạt; người dùng
đã yêu cầu dừng tự động và tự nạp tay. Xem [bộ firmware và hướng dẫn nạp tay](../../docs/testing/manual-firmware-2026-11-18.md)
để lấy merged BIN, SHA-256 và hiểu ảnh hưởng partition/NVS. Logger build từ source
bàn giao dùng 921600 baud, khác logger cũ 460800; chưa nạp/chạy binary mới trên bo.
Mỗi lần dùng hãy quét lại cổng; COM5 là inventory
của lần kiểm tra này. Logger thu dữ liệu và sensor_simulation trả RESULT là
hai chế độ firmware riêng, không chạy đồng thời trên cùng bo.

Tài liệu này hướng dẫn **đo thật** và **đối chiếu với mô phỏng**: firmware
logger trên ESP32 đọc radar LD2417 (UART) và IMU BMI160 (I2C), ghi ra một
log duy nhất có **cùng một đồng hồ** cho cả hai luồng. Máy tính tách log đó
thành hai file CSV mà app mô phỏng đọc được.

Bốn kịch bản cần thu: **đi thẳng**, **rẽ trái**, **rẽ phải**, **rung đường**.

---

## 0. Tóm tắt nhanh

| Bước | Việc | Lệnh |
|---|---|---|
| 1 | Nối cứng | xem mục 2 |
| 2 | Nạp firmware logger | `platformio run -e sensor_logger -t upload` |
| 3 | Ghi log trên PC | `python capture_hardware.py --port COM5 --out captures` |
| 4 | Đối chiếu | `python compare_capture.py --capture captures --scenario straight` |
| 5 | Phát lại trong app | xem mục 7 |

---

## 1. Vì sao phải dùng **một** đồng hồ cho cả hai luồng

Log radar và log IMU phải mô tả **cùng một khoảnh thời gian thực**. Nếu
lấy timestamp ở hai nơi khác nhau (ví dụ radar đọc bằng đồng hồ PC, IMU
đọc bằng đồng hồ ESP32) thì phải hiệu chỉnh thủ công, và sai số sẽ tích
lũy theo thời gian.

Firmware `sensor_logger` giải quyết bằng cách đóng dấu thời gian **mọi
bản ghi từ cùng một bộ đếm `micros()` của ESP32**:

```
RADAR,<ms>,<frame>,<id>,<huong>,<y_m>,<v_kmh>,<goc>,<cao_toc>,<lien_ket>,<ton_tai>
IMU,<timestamp_us>,<ax_g>,<ay_g>,<az_g>,<gx_dps>,<gy_dps>,<gz_dps>,<temperature_C>
SCENARIO,<timestamp_us>,<start|end>
```

Cột `ms` của radar và cột `timestamp_us` của IMU **dùng chung một gốc**.
Nhờ vậy máy tính không bao giờ phải trừ đồng hồ PC với đồng hồ ESP32.

---

## 2. Nối cứng

### 2.1. LD2417 → UART1

Module LD2417 dùng mức TTL 3.3 V, mặc định **115200 baud, 8N1**.

| Chân LD2417 | Chân ESP32 | Ghi chú |
|---|---|---|
| 1 VCC | 5V (VIN) | Module cần 5–12 V, dòng tối thiểu ~300 mA |
| 2 GND | GND | **Bắt buộc chung đất** |
| 3 TXC | **GPIO17 (RX2)** | Module phát dữ liệu |
| 4 RXC | GPIO16 (TX2) | Module nhận lệnh cấu hình (không bắt buộc khi chỉ ghi) |
| 5 IO1 | — | Không dùng |
| 6 IO2 | — | Không dùng |
| 7 CANH / 8 CANL | — | Không dùng |

ESP32-D0WD-V3: **UART0** là cầu USB CP210x (dùng để ghi log), **UART1**
(GPIO9/GPIO10) để dành, **UART2** là UART trống duy nhất nên logger dùng
UART2. Do đó chân phải là **GPIO17 = RX2** và **GPIO16 = TX2**.

Chân có thể đổi trong `firmware/src/sensor_logger.cpp` (hằng `RADAR_RX_PIN`,
`RADAR_TX_PIN`).

> **Quan trọng:** board chỉ có **một** LD2417, nên log chỉ ghi được **một
> phía** radar. App mô phỏng cố ý yêu cầu **cả hai** radar phải còn mới,
> nên khi phát lại log một radar, firmware sẽ báo `RADAR_STALE`. Đây là hành
> vi đúng, **không phải lỗi** — và logger không bao giờ tự bịa khung rỗng
> cho phía còn lại.

### 2.2. BMI160 → I2C

| Chân BMI160 | Chân ESP32 | Ghi chú |
|---|---|---|
| VDD | 3V3 | Kiểm tra board của bạn dùng 3.3 V hay 5 V cho BMI160 |
| GND | GND | Chung đất |
| SDI / SDA | **GPIO21 (SDA)** | |
| SCL | **GPIO22 (SCL)** | |
| ADDR | GND | Địa chỉ I2C **0x68**. Nối lên 3V3 để thành 0x69 |
| INT1 / INT2 | — | Không dùng (firmware đọc theo chu kỳ) |

Firmware dò chip ID ở thanh `0x00`; giá trị đúng của BMI160 là **0xD1**.
Nếu không tìm thấy chip, log sẽ ghi `BMI160 NOT FOUND`.

### 2.3. Xác nhận chiều lắp

BMI160 phải được khai báo chiều lắp **trước khi phát lại log**, vì sai
trục sẽ làm cảnh báo trái thành phải. Xem [mục 7.3](#73-khai-báo-chiều-lắp-bmi160).

---

## 3. Nạp firmware logger

Firmware logger nằm ở environment `sensor_logger`:

```powershell
# Đường dẫn workspace có khoảng trắng và dấu ngoặc -> dùng subst
subst R: "C:\MY_DATA (PRIVATE DATA)\My_Projects\RideGuard"

& firmware/.venv/Scripts/python.exe -m platformio run -d R:\firmware -e sensor_logger -t upload
```

> **Nạp firmware này sẽ ghi đè chương trình đang chạy trên board.**
> `sensor_simulation` (firmware app mô phỏng) sẽ bị thay. Nạp lại bằng
> `-e sensor_simulation -t upload` khi quay về dùng app.

Nếu lỗi kết nối (board không nhận lệnh flash): **giữ nút BOOT** rồi bấm
**EN**, thả BOOT sau khi bắt đầu upload. Trên board CP210x đã dùng ở đây
thì chỉ cần giữ BOOT khi báo lỗi `Failed to connect`.

Sau khi nạp, mở terminal để xem log:

```powershell
& firmware/.venv/Scripts/python.exe -m platformio device monitor -d R:\firmware -e sensor_logger
```

Bạn sẽ thấy:

```
# RideGuard hardware logger v1
# radar=LD2417 uart=115200 imu=BMI160 i2c=0x68 imu_hz=100
# RADAR rows: ms,frame,id,huong,y_m,v_kmh,goc,cao_toc,lien_ket,ton_tai
# IMU rows:   timestamp_us,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temperature_C
# Both share one origin: the first timestamp written.
# BMI160 ready
# BMI160 chip_id=0xD1 range=+/-2g gyro=+/-2000dps odr=100Hz
# READY
```

Nếu thấy `BMI160 NOT FOUND`, kiểm tra lại SDA/SCL và địa chỉ 0x68/0x69.

---

## 4. Ghi log trên máy tính

### 4.1. Lệnh

```powershell
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 capture_hardware.py --port COM5 --out captures --duration 300
Pop-Location
```

| Tham số | Ý nghĩa |
|---|---|
| `--port` | Cổng serial của board (`COM5` ở đây) |
| `--baud` | Mặc định `921600` (khớp firmware) |
| `--out` | Thư mục chứa kết quả |
| `--duration` | Số giây tối đa. `0` = ghi tới khi dừng bằng tay |

### 4.2. Đánh dấu kịch bản

Trong lúc ghi, gõ vào cửa sổ terminal:

| Phím | Tác dụng |
|---|---|
| `s` + Enter | Bắt đầu một kịch bản |
| `e` + Enter | Kết thúc kịch bản |
| `q` + Enter | Dừng ghi |

Mỗi khoảng đánh dấu sinh ra một cặp file riêng:
`radar_scenario_00.csv` + `imu_scenario_00.csv`.

Nếu không đánh dấu, toàn bộ bản ghi được ghi thành `radar_session.csv` +
`imu_session.csv`.

### 4.3. Chuỗi thu đề xuất

Mỗi kịch bản nên dài **≥ 30 s** để có đủ dữ liệu so sánh, và nên làm
trên đoạn đường thẳng, vạn xe đông:

1. **Đứng yên 5 s** (xe không chạy) — kiểm tra `az ≈ 1.0 g`, `gz ≈ 0`.
2. **Đi thẳng 30 s** — đường thẳng, tốc độ ổn định.
3. **Rẽ trái** — vào cua trái, giữ lái 3–5 s, rồi thẳng lại.
4. **Rẽ phải** — tương tự.
5. **Rung đường** — đoạn đường sần sùi, ổ gà, hay đi qua khe giữa hai
   tấm bê tông.

Bấm `s` trước khi bắt đầu mỗi động tác, `e` ngay sau khi kết thúc.

### 4.4. File sinh ra

```
captures/
  capture_session.log              # log thô, giữ lại làm bằng chứng
  capture_session.comments.txt     # các dòng # của firmware
  radar_scenario_00.csv            # định dạng Hi-Link
  imu_scenario_00.csv              # định dạng BMI160
```

**Hai file CSV giữ nguyên đồng hồ gốc** — không dời riêng từng file. Đây là
điều kiện để hai luồng còn là một timeline.

---

## 5. Đối chiếu với mô phỏng

```powershell
Push-Location tools/rideguard-simulator
& .venv/Scripts/python.exe -X utf8 compare_capture.py --capture captures --scenario scenario_00
Pop-Location
```

Công cụ báo:

```
=== Capture: scenario_00 (captures) ===
expected      : straight
radar frames  : 185
IMU samples   : 1201
radar cadence : median 65.0 ms, max 65 ms, min 65 ms
IMU cadence   : median 10.0 ms, max 10 ms, min 10 ms

yaw lọc (firmware 80 ms): mean +0.00 dps, peak 0.00 dps, rms 0.00 dps
gyro z thô            : mean +0.00 dps, peak 0.00 dps
az (m/s2)             : mean 9.81, peak |a| 9.81

motion (mô phỏng state machine của firmware): LEFT 0% • RIGHT 0% • STRAIGHT 100%

radar targets : 185
  distance   : 10.21 .. 12.00 m
  speed      : 7.50 .. 8.50 km/h
  angle      : -21 .. -14 deg
  lane       : trái

=== Đối chiếu với mô phỏng ===
  [OK  ] yaw gần 0 khi đi thẳng (mean yaw +0.00 dps)
  [OK  ] firmware giữ motion=STRAIGHT (LEFT 0 mẫu, RIGHT 0 mẫu)
  [OK  ] IMU không bị đứt quãng (max gap 10 ms)
  [OK  ] radar có dữ liệu (185 khung)

Tất cả khớp với kỳ vọng "straight".
```

### 5.1. Các kiểm tra và ý nghĩa

| Kịch bản | Kỳ vọng | Nếu FAIL |
|---|---|---|
| `straight` | yaw ≈ 0, motion luôn `STRAIGHT` | Có rung lẫn vào, hoặc mount sai |
| `left` | yaw dương, firmware nhận `LEFT` không có `RIGHT` | Dấu yaw ngược → đảo `yaw=-1` |
| `right` | yaw âm, firmware nhận `RIGHT` không có `LEFT` | Dấu yaw ngược → đảo `yaw=-1` |
| `bumpy` | gyro thô rung mạnh, firmware **không** nhận LEFT/RIGHT | Rung mạnh đủ vượt ngưỡng → xem mục 6 |

Tên kịch bản được suy ra tự động từ tên file. Ghi đè bằng `--expect`:

```powershell
& .venv/Scripts/python.exe -X utf8 compare_capture.py --capture captures --scenario scenario_00 --expect bumpy
```

### 5.2. `motion` là gì?

Cột `motion` trong báo cáo **không phải** đo trực tiếp từ firmware; nó là
bản mô phỏng lại **đúng state machine** của firmware trên log IMU đã đo:

1. Lọc thông thấp yaw 80 ms (giống `sim_state.hpp`).
2. Vượt ngưỡng 2 dps → thành *candidate* `LEFT`/`RIGHT`.
3. Candidate giữ nguyên trong **200 ms** → `motion` mới thực sự đổi.

Nhờ vậy câu hỏi "firmware có tin là xe đang rẽ không?" được trả lời trên
**chính dữ liệu đo được**, không phải trên dữ liệu tổng hợp.

---

## 6. Khi đối chiếu FAIL

### 6.1. Rung đường bị nhận nhầm là rẽ

Khi log `bumpy` cho thấy firmware nhận LEFT/RIGHT, rung đường đủ mạnh để
lọc 80 ms vẫn giữ trên ngưỡng. Hai hướng xử lý:

- **Tăng `yaw_threshold_dps`** trong app (mặc định 2 dps, cho phép tới 30).
  Khi thay đổi, phải ghi lại giá trị vào báo cáo — đây là tham số an toàn,
  không nâng tùy tiện.
- **Lọc thêm ở tầng cảm biến** nếu BMI160 đang gắn trên bề mặt rung mạnh.

Đừng "sửa" bằng cách cắt bớt dữ liệu — cần giữ nguyên bản ghi để còn
đối chiếu được.

### 6.2. Dấu yaw ngược

`mean yaw` âm khi rẽ trái (hoặc ngược lại) → BMI160 đang lắp ngược chiều
yaw. Dùng mount `identity,yaw=-1` (mục 7.3).

### 6.3. `az` lệch

Khi đứng yên mà `az` khác `1.0 g`:

- `az ≈ -1.0` → board đang lắp úp → dùng mount có dấu `-` trên trục Z.
- `az ≈ 0` → BMI160 không cấp nguồn hoặc I2C đọc sai.

### 6.4. IMU bị đứt quãng

`max gap > 200 ms` nghĩa là log bị mất mẫu. Kiểm tra lại nguồn, dây I2C,
và tỉ lệ log (921600 baud cho ~100 mẫu/s + 15 khung radar/s là dư).

---

## 7. Phát lại log trong app mô phỏng

### 7.1. Nạp lại firmware app

```powershell
& firmware/.venv/Scripts/python.exe -m platformio run -d R:\firmware -e sensor_simulation -t upload
```

### 7.2. Nhập vào app

1. Mở app, bấm **Quét cổng**, chọn board, **Kết nối**.
2. **Bấm checkbox `Log đồng bộ (radar + IMU cùng đồng hồ)`** — đây là điều
   kiện quan trọng nhất. Khi bật, app **không dời riêng** file radar và
   file IMU, nên hai luồng vẫn là một timeline.
3. **Nhập CSV / log D/E** → chọn `radar_scenario_00.csv`. Chọn `LEFT` hoặc
   `RIGHT` tuỳ vào log (LD2417 ghi cả hai phía trong cùng một file, nên
   chọn `LEFT` là đủ).
4. **Nhập log IMU (BMI160)** → chọn `imu_scenario_00.csv`.
5. **Lệch đồng hồ IMU** → để **0 ms** với log đồng bộ.
6. **Bắt đầu / Reset phiên**.

Panel **Nguồn dữ liệu** sẽ hiện:
`RADAR + IMU GHI THỰC • ĐỒNG BỘ CÙNG ĐỒNG HỒ • mount identity • lệch đồng hồ 0 ms`

### 7.3. Khai báo chiều lắp BMI160

Ô **Gắn BMI160** nhận các giá trị:

| Giá trị | Ý nghĩa |
|---|---|
| `identity` | Trục cảm biến đã là trục xe (X tiến, Y trái, Z lên) |
| `xyz<-x,z,y` | Board đặt nằm ngang: X xe ← X cảm biến, Y xe ← Z cảm biến, Z xe ← Y cảm biến |
| `xyz<-x,y,z` | Giống identity nhưng ghi rõ từng trục |
| `xyz<-x,-y,z` | Đảo chiều trục Y cảm biến |
| `identity,yaw=-1` | Đảo chiều yaw (board úp ngược) |

Cú pháp: `xyz<-<trục xe 1>,<trục xe 2>,<trục xe 3>`, mỗi trục cảm biến có
thể ghi kèm dấu `-` để đảo chiều. Thêm `,yaw=-1` để đảo chiều yaw.

> Firmware **không** tự đoán trục. Nếu không khai báo, cảnh báo có thể bị
> đảo trái–phải.

### 7.4. Về mô phỏng tổng hợp

Bấm **Về mô phỏng tổng hợp** và **Bỏ log IMU** để quay lại dữ liệu tổng hợp.

---

## 8. Kiểm thử pipeline khi chưa có phần cứng

Để kiểm tra toàn bộ chuỗi (ghi → tách → đối chiếu → phát lại) mà không
cần board, dùng bộ dữ liệu **giả lập**:

```powershell
Push-Location tools/rideguard-simulator

# Sinh log giả lập đúng định dạng firmware
& .venv/Scripts/python.exe -X utf8 make_synthetic_capture.py --out captures_synthetic

# Đối chiếu cả 4 kịch bản
foreach ($s in @('straight','left_turn','right_turn','bumpy')) {
  & .venv/Scripts/python.exe -X utf8 compare_capture.py --capture captures_synthetic --scenario $s
}

Pop-Location
```

> Dữ liệu sinh ra là **giả lập**, chỉ để kiểm thử phần mềm. Không dùng
> làm bằng chứng đo thực.

---

## 9. Tham chiếu kỹ thuật

### 9.1. Giao thức LD2417

Lấy từ tài liệu chính thức **HLK-LD2417串口协议说明V1.0.4.pdf**:

```
AA AA | N | target 1 .. target N | 55 55
```

Mỗi target đúng **10 byte**, little-endian:

| Byte | Trường | Diễn giải |
|---|---|---|
| 0 | `id` | Số định danh track |
| 1 | `dir` | `1` = làn trái, `2` = làn phải |
| 2–3 | `y_m` | uint16, `/100` → mét |
| 4–5 | `v_kmh` | uint16, `/100` → km/h |
| 6–7 | `state` | bit0 = tốc độ cao, bit1 = đã liên kết, bit8–15 = số frame tồn tại |
| 8–9 | `angle` | int16, độ (có dấu) |

> **Khác LD2412/LD2410B.** LD2412 dùng khung `F4 F3 F2 F1` + độ dài +
> `F8 F7 F6 F5`. LD2417 dùng `AA AA ... 55 55` và **không có** trường độ
> dài. Hai protocol này không dùng chung decoder.

### 9.2. BMI160

| Mục | Giá trị |
|---|---|
| Chip ID | `0x00` → `0xD1` |
| Địa chỉ I2C | `0x68` (ADDR nối đất) / `0x69` (ADDR lên 3V3) |
| Dữ liệu gyro | `0x0C` |
| Dữ liệu accelerometer | `0x12` |
| Nhiệt độ | `0x20` |
| `ACC_RANGE` | `0x41` = `0x03` (±2 g) |
| `GYR_RANGE` | `0x43` = `0x00` (±2000 °/s) |
| `ACC_CONF` / `GYR_CONF` | `0x40` / `0x42` = `0x28` (ODR 100 Hz, bộ lọc thường) |
| Lệnh | `0x7E`: accel normal `0x11`, gyro normal `0x15` |
| Độ nhạy accelerometer | 16384 LSB/g (±2 g) |
| Độ nhạy gyro | 16.4 LSB/(°/s) (±2000 °/s) |
| Nhiệt độ | 0.002 K/LSB, lệch 23 °C |

### 9.3. Quy tắc firmware dùng khi đối chiếu

| Tham số | Giá trị | Nguồn |
|---|---|---|
| Lọc yaw | thông thấp 80 ms | `firmware/include/sim_state.hpp` |
| Ngưỡng yaw | 2 dps (mặc định) | `sim_rules.hpp` |
| Xác nhận rẽ | 200 ms | `sim_rules.hpp` |
| Giữ cảnh báo | 350 ms | `sim_rules.hpp` |
| Vùng gần | 18 m | `sim_rules.hpp` |

---

## 10. Giới hạn đã biết

- Chỉ **một** LD2417, nên log chỉ có một phía radar. Firmware app yêu cầu
  cả hai phía còn mới → `RADAR_STALE` khi phát lại log một radar.
- `angle` của LD2417 là **góc trong ±60°** của module, không phải góc phía
  so với chính xe. Chưa có dữ liệu đủ để hiệu chỉnh tỉ lệ này, nên giá
  trị được giữ nguyên khi nhập.
- BMI160 chưa được hiệu chỉnh zero-rate; giá trị yaw khi đứng yên có thể
  trôi vài dps. Đo đường thẳng 30 s giúp đánh giá độ trôi này.
- Không có gyro Z gắn vào góc nghiêng thân xe, nên `heading_deg` của
  firmware tích phân từ yaw, không đo trực tiếp.
