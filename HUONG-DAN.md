# Hướng dẫn sử dụng RideGuard Sensor Lab

Cập nhật 19/11/2026. Đây là app desktop mô phỏng 2D và phát lại dữ liệu radar/IMU qua USB Serial. Hướng dẫn này dành cho **Sensor Lab**, không cần khởi chạy backend, web, mobile, PostgreSQL hay Docker.

**Mới: [AI trên PC và so sánh hai màn hình](../docs/ai/sensor-lab.md)**. Có chọn session RideGuard_Synthetic_v1, một stream chung cho ESP32/AI, Pause/Resume và xem lại sau STOP. Đọc [model card](../docs/ai/model-card-synthetic-v1.md) trước khi diễn giải điểm mô hình; không dùng kết quả tổng hợp như xác nhận an toàn thực tế.

[Timeline cảnh báo và đoạn bất đồng](../docs/ai/so-sanh-timeline.md): ESP32/AI/nhãn trái–phải cùng timestamp, nhảy tới đoạn khác nhau sau STOP, HIGH đầu/tổng thời gian và chấm bỏ sót/báo nhầm với nhãn. Có lệnh chạy đủ 20 phiên test; mức đồng ý không phải độ chính xác.

Máy tính tạo/đọc dữ liệu cảm biến → gửi bản tin xuống ESP32 → ESP32 xử lý và trả `RESULT` → app hiển thị đèn trái/phải. **Đèn bên Bộ luật ESP32 chỉ lấy từ RESULT thật của ESP32**. Chạy offline vẫn xem được hoạt ảnh và dữ liệu đầu vào, nhưng không có kết luận cảnh báo từ bo. Khi bật so sánh, đèn AI riêng lấy từ model PC, không lấy nhãn CSV hoặc RESULT.

## 1. Mở app từ đầu

### Đã có môi trường Python của dự án

Mở terminal PowerShell tại **gốc repository RideGuard** (thư mục có `scripts`, `tools`, `firmware`), chạy:

```powershell
.\scripts\run-simulator.ps1
```

Hoặc nếu terminal đang ở **tools/rideguard-simulator**:

```powershell
.venv/Scripts/python.exe -X utf8 -m rideguard_sim
```

Chọn một cách, không cần mở hai app cùng lúc. `-X utf8` giúp tiếng Việt trong log hiển thị đúng. Để tắt app, đóng cửa sổ; app sẽ đóng Serial và log của chính nó.

### Chỉ khi máy chưa có venv

Từ gốc repo:

```powershell
python -m venv tools/rideguard-simulator/.venv
& tools/rideguard-simulator/.venv/Scripts/python.exe -m pip install -r tools/rideguard-simulator/requirements.txt
.\scripts\run-simulator.ps1
```

Cần Python 3.11 trở lên. Không cài lại dependency mỗi lần mở app.

### Điều kiện để nhận kết quả từ ESP32

Bo phải đang chạy firmware **sensor_simulation**, giao tiếp **921600 baud, 8N1**. Firmware logger dùng để thu cảm biến là chương trình khác; đổi baud trên app không biến logger thành bộ xử lý RESULT.

Bạn tự nạp bằng [hướng dẫn firmware nạp tay](../docs/testing/manual-firmware-2026-11-18.md). Có sẵn merged BIN và BIN rời. Đọc phần ảnh hưởng partition/NVS trước khi nạp. Backup toàn flash logger cũ chưa đạt; không có quy trình tự backup/upload đang chạy.

COM5 là cổng được phát hiện trong lượt kiểm tra trước, không phải giá trị cố định. Cắm lại bo có thể đổi COM. Đóng Serial Monitor, công cụ capture hoặc harness đang giữ cổng trước khi bấm Kết nối; không chạy nhiều chương trình trên cùng COM.

## 2. Hiểu bố cục màn hình

- **Cột trái:** chọn cổng, nguồn dữ liệu, điều khiển phiên, cấu hình xe/cảm biến. Cuộn cột trái xuống để thấy các nút nằm phía dưới.
- **Giữa:** minh họa xe, làn đường, vị trí mục tiêu và vùng radar. Với CSV IMU thu thực, EGO chỉ minh họa; app không tích phân IMU để khẳng định quỹ đạo chính xác.
- **Cột phải — DỮ LIỆU ĐANG GỬI:** bản tin đầu vào gần nhất do PC chuẩn bị. Có dữ liệu ở đây chưa chứng minh bo đã nhận hoặc xử lý.
- **IMU preview và đồ thị:** sáu thành phần gia tốc/gyro trong hệ trục đã chọn. Gia tốc dùng m/s², gyro dùng °/s; trục ngang đồ thị dùng giây. Preview không phải RESULT từ bo. Giảm số điểm để vẽ không làm mất mẫu gửi.
- **KẾT QUẢ THỰC TỪ ESP32:** RESULT gần nhất còn hợp lệ; trạng thái mỗi phía, mã nguyên nhân, radar và track liên quan.
- **Dưới cùng:** Hiện/ẩn log và Xuất log TX/RX + cấu hình. Khi có lỗi app sẽ tự mở panel log.

Các trạng thái đèn:

- `NONE`: không có mục tiêu trong các khung hợp lệ.
- `MONITOR`: có mục tiêu cần theo dõi; chưa đạt điều kiện HIGH.
- `HIGH`: bộ luật trên ESP32 kết luận cảnh báo cao cho phía đó.
- `UNKNOWN` hoặc dấu hỏi: thiếu dữ liệu, dữ liệu cũ, PAUSE, chưa có phiên hoặc chưa có RESULT. Không hiểu UNKNOWN là an toàn.

Dữ liệu RESULT quá một giây không được dùng như phản hồi mới. `sim_age_ms` là tuổi theo thời gian mô phỏng; `wall_age_ms` là tuổi theo thời gian thực của bo. PAUSE dừng mô phỏng nhưng thời gian thực vẫn trôi.

## 3. Cách thử nhanh, chưa cần CSV

### Chỉ xem offline

1. Mở app, chưa bấm Kết nối.
2. Chọn kịch bản ở nhóm **2 · NGUỒN / ĐIỀU KHIỂN**.
3. Bấm **Bắt đầu / Reset phiên**. Xem xe và dữ liệu tổng hợp.
4. Đèn vẫn thể hiện chưa có phản hồi ESP32. Đó là đúng khi offline.
5. Bấm **Dừng phiên** trước khi đổi nguồn dữ liệu.

### Thử xử lý trên bo thật

1. Quét cổng → chọn đúng bo → Baud **921600** → Kết nối.
2. Đợi nhãn kết nối hết báo đang chờ khởi động/dọn nhiễu boot ROM.
3. Chọn kịch bản, bấm **Bắt đầu / Reset phiên**.
4. Mở log: cần có `TX SESSION_START`, rồi `RX ACK` với `command=SESSION_START`, cùng `session_id` và `seq`. State chuyển **RUNNING**, tiếp theo có `RX RESULT`.
5. Nếu ACK timeout, chưa thể nghiệm thu cảnh báo: kiểm tra firmware, baud, reset và chương trình khác giữ COM.
6. Thử **Xe phía sau trái tiến gần** để quan sát MONITOR khi đi thẳng; thử **Xe trái tiến gần → chuyển làn trái** để xem có HIGH trái trong điều kiện phù hợp. Đèn thực tế phụ thuộc dữ liệu/luật, không được ép theo tên kịch bản.
7. Tạm dừng/Chạy tiếp/Dừng phiên đều chờ ACK khi đang nối bo. Reset phiên tạo session mới.

## 4. Nạp radar CSV và IMU CSV đúng thứ tự

### Bộ mẫu để bắt đầu

- Radar: `tools/samples/generated/left_approach_45s_dual.csv` — có cả LEFT và RIGHT, gần 45 giây.
- IMU mới: `tools/samples/imu14-v1/lane_left-45s-200hz.csv` — 9000 mẫu, 200 Hz, chuyển làn trái tổng hợp rồi trả hướng.
- IMU tham chiếu: `tools/samples/reference/user-provided-14rows.csv` — đúng 14 dòng bạn cung cấp, chỉ **64,985 ms**, không phải log 45 giây.

Bộ radar và IMU chọn từ các file riêng là **nguồn ghép thử nghiệm**, chưa có bằng chứng thu/sinh đồng bộ. Không coi cùng thời lượng là cùng đồng hồ. Cặp radar/IMU mẫu trên đã truyền đủ mẫu trong lượt thử nhưng không quan sát HIGH; đó không phải bằng chứng file bị hỏng.

### Các bước trong app

1. **Dừng phiên**, chờ STOP ACK. Nếu trước đó đang ERROR, xử lý lỗi/kết nối lại trước khi chạy.
2. Đặt **Tốc độ thời gian = 0.5×** khi dùng IMU 200 Hz với radar mặc định. 1× hiện bị từ chối theo ngân sách Serial bảo thủ; không tự bỏ mẫu.
3. Nếu là hai file mẫu riêng: để **Log đồng bộ (radar + IMU cùng đồng hồ)** ở trạng thái **tắt**. Offset ban đầu **0.000 ms**. Đây chỉ là căn dòng đầu của từng file, chưa phải đồng bộ được kiểm chứng.
4. Chọn **CSV thuộc radar = LEFT**, bấm **Nhập CSV / log D/E**, chọn file radar. Nếu file có cột `radar_id` LEFT/RIGHT thì importer lấy theo từng dòng; với log một radar không có cột đó, ô này cho biết file thuộc radar gắn phía nào. `huong` trong log không thay cho `radar_id`.
5. Chọn **Profile CSV IMU** trước khi nhập:
   - `user_table_v1`: dùng cho bản tham chiếu 14 dòng và các file mới trong `imu14-v1`. Kiểm đúng header, RAW int16 và quan hệ RAW/đơn vị theo hệ số từ bảng bạn gửi; chưa xác minh model chip.
   - `physical_only`: dành cho mẫu cũ `generated/imu_bmi160_*.csv` hoặc capture có cột vật lý nhưng không biết scale RAW. Không kiểm hệ số RAW; không chọn chỉ để giấu lỗi của một profile đã biết.
6. Chọn **Ánh xạ trục IMU** và **Mount roll/pitch/yaw** đúng cách lắp. Mẫu mới mặc định identity và các góc bằng 0. Không tự suy ra hướng lắp từ dấu của `az`.
7. Bấm **Nhập CSV IMU 14 cột**, chọn file IMU. Nếu nhập thành công, nhãn sẽ hiện số mẫu, thời lượng, rate, nguồn và đơn vị; preview/đồ thị có dữ liệu. Đây chưa phải gửi xuống bo.
8. Với file 14 dòng, thấy **14 mẫu, 64.985 ms, ~200.046 Hz** là nhập thành công. Chữ `ms` chỉ là đơn vị mili-giây, **không phải mã lỗi**. 1000 ms = 1 giây; 64.985 ms = 0.064985 giây.
9. Muốn phát thật: kết nối đúng bo, bấm **Bắt đầu / Reset phiên**, kiểm START ACK và RESULT. Đổi profile/mount/chế độ clock sau khi nhập thì phải nhập lại CSV; offset được áp dụng khi START.
10. Khi cả radar và IMU hết bản ghi, app dừng phiên. Nếu một nguồn hết trước, không tự bịa thêm dữ liệu; bo có thể báo stale/UNKNOWN trong lúc chờ nguồn còn lại.

Chỉ nhập IMU cũng được để preview. Nếu START khi chưa nhập radar CSV, radar/hoạt ảnh có thể là tổng hợp; app ghi nguồn ghép, EGO không phải quỹ đạo được khôi phục từ IMU.

### Các file cũ dễ nhầm

- `generated/imu_bmi160_45s.csv` và `imu_bmi160_synthetic.csv`: scale khác profile mới, gyro +8 cố định. Chúng mô phỏng quay trái đều, không chứng minh chuyển làn hoàn chỉnh. Dùng `physical_only` nếu chủ động thử cột vật lý của những file đó.
- `generated/empty_frames_45s_dual.csv`: thực tế span 22,425 giây và không có khung rỗng. Không dùng tên file làm bằng chứng mất radar.
- `radar_recorded_excerpt.csv`: 7 khung trong 378 ms và chỉ một phía khi gán LEFT. Thiếu radar còn lại có thể cho UNKNOWN.

## 5. Tác dụng của từng ô input và nút

### Nhóm 1 — KẾT NỐI ESP32

- **Cổng:** chọn thiết bị USB-UART vừa quét, ví dụ CP210x COM5. Sai COM sẽ không nói chuyện với đúng bo.
- **Baud:** tốc độ Serial PC–bo. `sensor_simulation` cần **921600**. Không dùng ô này để chữa một CSV trùng thời gian.
- **Quét cổng:** cập nhật danh sách thiết bị hiện cắm.
- **Kết nối / Ngắt kết nối:** mở/đóng cổng. Kết nối thành công chưa đồng nghĩa có ACK ứng dụng.
- **Nhãn trạng thái:** phân biệt offline, đang chờ board khởi động và Serial đã mở.

### Nhóm 2 — NGUỒN / ĐIỀU KHIỂN

- **Danh sách kịch bản:** chọn mô hình xe tổng hợp. Khi dùng radar/IMU CSV, nội dung file quyết định các mẫu nhập; tên kịch bản không gán nhãn cho CSV.
- **CSV thuộc radar:** phía lắp LEFT/RIGHT cho log một radar chưa có `radar_id`.
- **Nhập CSV / log D/E:** nạp nguồn radar; không nạp IMU bằng nút này.
- **Về mô phỏng tổng hợp:** xóa lựa chọn nguồn replay radar/IMU trong app và dùng mô hình tổng hợp. Không xóa file trên đĩa. Cần STOP trước.
- **Replay + IMU giả lập (chưa đồng bộ):** thêm IMU từ mô hình khi phát radar CSV mà chưa nhập IMU riêng. Nếu đã có IMU CSV thì IMU CSV được ưu tiên. Nguồn ghép chưa được chứng minh đồng bộ.
- **Profile CSV IMU:** lựa chọn quy tắc kiểm tra như mục 4. Profile không tự đổi đơn vị của file để làm nó đạt.
- **Ánh xạ trục IMU:** gán trục cảm biến sang trục xe. Trục xe: X tiến, Y trái, Z lên. `identity` giữ nguyên; `xyz<-x,z,y` là X xe lấy X cảm biến, Y xe lấy Z, Z xe lấy Y. Các mapping đổi dấu là compatibility; không dùng thay phép lắp vật lý chưa xác nhận.
- **Mount roll/pitch/yaw (độ):** phép quay sensor→body `Rz(yaw) Ry(pitch) Rx(roll)`, áp sau ánh xạ trục. Với CSV mới có mount trong metadata, nhập cùng góc đó để đưa dữ liệu về trục xe. Thay góc rồi nhập lại file.
- **Lệch đồng hồ IMU (ms):** cộng vào thời gian IMU trước khi ghép với radar; số dương làm IMU đến muộn hơn, âm làm sớm hơn. `0.001 ms = 1 µs`, `1000 ms = 1 giây`. Offset làm timestamp âm hoặc hai mẫu trùng ms sẽ bị từ chối; không làm thay đổi CSV gốc.
- **Log đồng bộ:** chỉ bật khi có bằng chứng radar/IMU dùng cùng một clock và cần giữ mốc gốc. Checkbox là khai báo của người vận hành, không phải kết quả đo đồng bộ tự động. Bật/tắt sau khi nhập thì nạp lại cả nguồn tương ứng.
- **Nhãn IMU:** số mẫu, span đầu–cuối, rate ước lượng, nguồn, profile, mount, đơn vị. Một file danh nghĩa 45 giây ở 200 Hz có 9000 mẫu và span 44,995 giây theo quy ước `[0,45s)`.
- **Nhập CSV IMU 14 cột:** chỉ đọc/kiểm tra file và tạo preview; START mới phát dữ liệu.
- **Xuất CSV IMU từ mô hình phiên này:** xuất mẫu tổng hợp mà phiên đã tạo, đúng 14 cột và sidecar JSON. Nhịp xuất theo mô hình phiên (mặc định 100 Hz), không tự nâng thành 200 Hz. Không biến IMU thu thực thành quỹ đạo chính xác. File có sẵn không bị ghi đè.
- **Bỏ log IMU:** bỏ lựa chọn IMU nhập trong app, giữ file trên đĩa. Nếu tiếp tục radar replay, cần chọn rõ có dùng IMU giả lập hay không; thiếu IMU có thể UNKNOWN.
- **Tốc độ thời gian:** 0.5× chậm gấp đôi thời gian thực, 1× bình thường, 2× nhanh gấp đôi. Giữ timestamp/mẫu nguồn, không resample. Chọn trước START để kiểm băng thông; không tăng giữa phiên.
- **Bắt đầu / Reset phiên:** kiểm cấu hình rồi tạo phiên mới; gửi START và chờ ACK khi nối bo.
- **Tạm dừng / Chạy tiếp:** gửi PAUSE/RESUME, chờ ACK; dừng/chạy lại thời gian mô phỏng.
- **Dừng phiên:** gửi STOP, chờ ACK. Dùng trước khi đổi file/profile.
- **STOPPED/RUNNING/PAUSED/ERROR · chờ ACK:** trạng thái phiên. ERROR hoặc timeout không phải chạy thành công.

### Nhóm 3 — XE / QUỸ ĐẠO

Các ô này điều khiển mô hình tổng hợp; không sửa CSV và không áp lên dữ liệu cảm biến đã ghi.

- **Xe EGO/ID…:** chọn xe mình hoặc xe mục tiêu để đọc/chỉnh.
- **Dọc X (m):** vị trí dọc đường; **Ngang Y trái (m):** vị trí ngang, dương sang trái.
- **Tốc độ (km/h):** tốc độ của xe đang chọn. Trong mô hình và bản tin cảm biến sẽ đổi đơn vị khi cần.
- **Hướng (độ):** heading của xe; 0° theo hướng tiến của đường, dương về trái.
- **Thời gian chuyển làn (s):** thời gian cho chuyển làn trơn, trong giới hạn ô nhập.
- **Đọc xe đang chọn:** đổ giá trị hiện tại của xe vào các ô; chỉ đọc, chưa áp thay đổi.
- **Áp dụng khi tạm dừng:** chỉ có tác dụng khi PAUSED và không dùng radar replay. Sau chỉnh, RESUME để xe tiếp tục chạy.
- **Chuyển làn trái +3.5 m / phải −3.5 m:** yêu cầu mô hình xe đang chọn chuyển làn, có pha quay trả hướng. Dùng khi mô phỏng tổng hợp; không dùng để gán lại hướng chuyển động của IMU CSV.
- **Đi thẳng theo hướng đường:** khi PAUSED, đặt heading về 0 và kết thúc lệnh chuyển làn mô hình.
- **Khu vẽ:** lăn chuột để zoom; có thể kéo xe khi PAUSED và không replay radar. Đây là chỉnh mô hình, không thay dữ liệu ghi thực.

### Nhóm 4 — THAM SỐ (PHIÊN MỚI)

Các thay đổi có hiệu lực khi START phiên mới. Giữ mặc định khi đang kiểm tra lỗi nhập CSV để tránh trộn nhiều nguyên nhân.

- **Tầm mô phỏng (m), mặc định 40:** giới hạn khoảng cách radar tổng hợp. Không thay khả năng vật lý radar thật và không sửa target trong CSV.
- **Nửa góc quét (độ), mặc định 60:** vùng nhìn ±góc của radar tổng hợp. Không phải góc quay xe.
- **Chu kỳ radar (ms), mặc định 65:** khoảng thời gian giữa các khung tổng hợp; số nhỏ hơn phát thường xuyên hơn và tốn băng thông hơn. Radar CSV dùng timestamp trong file.
- **Chu kỳ IMU (ms), mặc định 10:** nhịp IMU tổng hợp, 10 ms tương ứng 100 Hz. IMU CSV dùng nhịp file; đổi ô này không biến CSV 200 Hz thành 100 Hz và không sửa lỗi trùng ms của CSV.
- **Giới hạn mục tiêu, mặc định 4:** số target tối đa của radar tổng hợp; cũng tham gia dự toán băng thông bảo thủ.
- **Nhiễu Y sigma (m):** mức nhiễu khoảng cách của radar tổng hợp; 0 là không thêm nhiễu.
- **Xác suất mất khung:** tỷ lệ bỏ khung trong nguồn tổng hợp để thử thiếu dữ liệu; 0 là không chủ động bỏ, 1 là luôn bỏ. Không sửa file radar đã nhập.
- **P(associated=0):** tỷ lệ mô phỏng target chưa liên kết; là cờ dữ liệu, không tự chứng minh target đúng/sai.
- **Ngưỡng yaw (độ/s), mặc định 2:** ngưỡng nhận chuyển hướng trong luật thử nghiệm; không phải góc bẻ ghi đông.
- **Xác nhận chuyển hướng (ms), mặc định 200:** thời gian phải duy trì chuyển hướng để xác nhận, giúp không phản ứng ngay với rung ngắn.
- **Vùng gần (m), mặc định 18:** khoảng gần được dùng trong luật HIGH khi kèm điều kiện hướng/heading phù hợp. Không có nghĩa cứ dưới 18 m là HIGH.

Các luật khác như hold 350 ms và stale 260 ms hiện không có ô chỉnh trong nhóm này. Không đổi ngưỡng để ép dữ liệu ghép thử nghiệm ra HIGH.

## 6. Báo lỗi IMU: đọc ở đâu, sửa thế nào?

### Nhận log đầy đủ

Khi thao tác lỗi, app hiện hộp thoại có nguyên nhân, tự mở panel log và ghi file:

```text
<log-dir>/app-errors-YYYYMMDD-HHMMSS-<id>.jsonl
```

Mặc định log-dir là **`.local/simulator-logs/` tính từ gốc repository**. Hộp thoại hiển thị đường dẫn tuyệt đối; bấm **Show Details/Hiện chi tiết** để xem thông tin có thể sao chép. Log lưu thời gian, thao tác, đường dẫn file vừa thử nhập, profile, mount, offset, tốc độ, thông báo và traceback. Không cần đã START hoặc nối ESP32 mới có log lỗi. Nếu thư mục không ghi được, hộp thoại báo riêng lỗi ghi log và vẫn giữ chi tiết trên màn hình.

Mở PowerShell từ gốc repo để xem các lỗi gần nhất:

```powershell
Get-ChildItem .local/simulator-logs/app-errors-*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Get-Content '<đường-dẫn-file-log-hiện-trong-hộp-thoại>' -Tail 5
```

Nếu cần hỗ trợ, gửi mã lỗi, đường dẫn CSV/tên file, profile, offset, tốc độ và phần message trong log. Không cần sửa CSV nhiều lần để thử đoán.

### Chỉ hiện “64.985 ms” ở nhãn IMU

Đây là **thời lượng**, không phải lỗi. Nếu bên cạnh có số mẫu/rate và đồ thị thì file đã nhập. File 14 dòng tham chiếu chỉ có khoảng 0,065 giây dữ liệu. Chưa có RESULT nếu chưa nối bo hoặc chưa START.

### IMU_TIMESTAMP_MS_COLLISION

Ví dụ thông báo: hai mẫu sau offset là `0 µs → 0 ms` và `500 µs → 0 ms`. Cả hai tăng theo µs nhưng sau khi sang đơn vị firmware lại trùng ms, trong khi firmware yêu cầu mỗi mẫu IMU có ms tăng nghiêm ngặt.

App ghi rõ số thứ tự hai mẫu, thời gian µs/ms và khoảng cách. Kiểm tra nguồn xuất timestamp, đơn vị và tần số. Mẫu 200 Hz có bước khoảng 5000 µs nên thường không gặp trùng ms; nếu dữ liệu bị gắn nhãn µs nhưng thực ra chứa ms thì cần xác nhận quy trình xuất, không tự nhân thời gian khi chưa rõ nguồn. **Giảm tốc độ 0.5× không sửa được trùng ms nguồn.** Nếu thật sự lấy mẫu dưới 1 ms, cần pipeline resampling có lọc riêng hoặc đổi hợp đồng; app không tự thêm một ms vào mẫu để che lỗi.

### IMU_TIMESTAMP_ORDER hoặc timestamp không nguyên

Có dòng timestamp trùng/giảm, hoặc không phải integer µs. Thông báo chỉ dòng và giá trị trước/sau. Kiểm tra log có bị nối lại, sắp xếp sai hoặc xuất từ Excel dưới dạng không phù hợp. Không tự sort rồi coi là đúng vì có thể trộn các lần ghi.

### IMU_TIMESTAMP_RANGE

Offset làm mẫu có thời gian âm hoặc vượt miền ms giao thức. Thông báo chỉ mẫu, thời gian sau offset và offset hiện tại. Thử lại offset 0 và kiểm chế độ giữ mốc gốc; xác định mốc căn chỉnh trước khi dùng offset âm. App không âm thầm bỏ các mẫu âm.

### IMU_HEADER

Header sai tên/thứ tự/số cột hoặc file dùng dấu `;` thay dấu `,`. Hộp thoại hiện header đang đọc và header đúng 14 cột. Kiểm tra CSV xuất bằng dấu phẩy và dấu chấm thập phân. Profile `physical_only` vẫn cần các cột vật lý và timestamp, không phải đọc mọi CSV tùy ý.

### IMU_RAW_UNIT / IMU_RAW_INT16

- RAW/unit không khớp: hiện dòng, cột, RAW, scale, giá trị kỳ vọng/thực và dung sai. `user_table_v1` dùng 0.000122 g/LSB và 0.0175 °/s/LSB theo bảng người dùng, chưa xác minh phần cứng. Chọn đúng profile theo nguồn; mẫu cũ có scale khác phải được xử lý như compatibility đã khai báo.
- RAW int16: giá trị phải là integer từ −32768 đến 32767. Không cắt hoặc cho số nguyên tràn để nhập thành công.
- Số không hữu hạn/ô trống: log chỉ dòng/cột liên quan; kiểm tra `nan`, `inf`, dấu thập phân hoặc dữ liệu thiếu.

### SERIAL_BUDGET

Đây là lỗi dự toán băng thông, không phải sai định dạng CSV. Ví dụ IMU 200 Hz + radar 65 ms/max4 ở 1× vượt ngưỡng bảo thủ. Chọn **Tốc độ thời gian = 0.5×** trước START rồi thử lại. CSV nguồn vẫn đủ 200 Hz; không bỏ mẫu, không đổi timestamp. Không tăng baud hoặc giảm chu kỳ IMU tổng hợp để che lỗi của file nhập.

### Metadata SHA-256 không khớp

Có file `<tên.csv>.json` đi kèm nhưng hash không còn khớp CSV. Có thể CSV bị sửa sau khi tạo metadata hoặc JSON thuộc file khác. Giữ bản gốc và xác nhận nguồn/xuất lại đúng cặp CSV–metadata; không sửa hash chỉ để vượt kiểm tra provenance.

### ACK timeout / không có RESULT

Kiểm tra bo đang chạy `sensor_simulation`, baud 921600, đúng COM và không có monitor khác giữ cổng. START phải được ACK. Lỗi này không chứng minh CSV sai. Không tự nạp/erase để thử trong lúc chưa xác định firmware đang chạy.

## 7. Tạo CSV IMU mới và xuất bằng chứng

Từ gốc repo, tạo file mới (không ghi đè file đã có):

```powershell
& tools/rideguard-simulator/.venv/Scripts/python.exe -X utf8 tools/rideguard-simulator/generate_imu_csv.py --scenario lane_left --duration 45 --rate 200 --seed 17 --output .local/my-new-lane-left.csv
```

Có các scenario `stationary`, `straight`, `lane_left`, `lane_right`, `steady_left`, `bumpy`, `tilted`. Generator ghi CSV và `.csv.json` với seed, nguồn SYNTHETIC, profile, mount và thời lượng. Chi tiết mô hình/đơn vị: [IMU-CSV.md](rideguard-simulator/IMU-CSV.md).

**Xuất log TX/RX + cấu hình** ở cuối app lưu các bản tin của phiên và cấu hình. Log phiên mặc định là `.local/simulator-logs/<ngày-giờ>-<session>.jsonl`; khác với file `app-errors-*.jsonl` lưu lỗi thao tác. Mở log bằng editor để thấy:

- `TX`: PC chuẩn bị bản tin gửi; trace đo đạc mới xác nhận thời điểm ghi transport.
- `RX ACK`: ESP32 xác nhận command ứng dụng.
- `RX RESULT`: kết quả bo trả về.
- `RX ERROR`: lỗi giao thức/input do bo báo.
- `META`: cấu hình và provenance; không phải cảm biến mới.

## 8. Mức kiểm chứng và giới hạn

Lượt kiểm tra trước đã có 128 test PC đạt, chọn file trên GUI Windows thật và replay 9000 mẫu trên ESP32: 899 RESULT, không parser error/mất sequence, gap PC max204,47 ms. Đó là bằng chứng cho cấu hình đã đo, không đảm bảo lỗi khoảng trống 1,3 giây đã biến mất trong mọi điều kiện. [Báo cáo CSV](../docs/testing/imu14-2026-11-18.md).

Lượt sửa thông báo/log lỗi này có báo cáo riêng: [hướng dẫn và chẩn đoán lỗi IMU](../docs/testing/imu-error-diagnostics-2026-11-18.md). Không tự nạp firmware hoặc chạy lại nghiệm thu phần cứng trong lượt chỉ sửa hướng dẫn/lỗi nhập.

Chưa nghiệm thu cảm biến thật, mount/calibration thật, HUD/rung hoặc an toàn giao thông. Các CSV trong `samples/generated` và `samples/imu14-v1` là tổng hợp; USER_PROVIDED chỉ mô tả nguồn người dùng cung cấp, không tự xác nhận nhãn/bo đo.

Các tài liệu tiếp theo:

- [Schema, profile, generator và offset](rideguard-simulator/IMU-CSV.md).
- [Nạp firmware bằng tay và ảnh hưởng dữ liệu](../docs/testing/manual-firmware-2026-11-18.md).
- [Thu log cảm biến thật](rideguard-simulator/THU-LOG-THAT.md).
- [Giao thức sensor_simulation](../contracts/sensor-simulation.md).
