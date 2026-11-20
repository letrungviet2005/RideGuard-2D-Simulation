# Script tiện ích Sensor Lab

Ứng dụng GUI vẫn chạy qua `scripts/run-simulator.ps1` ở **gốc repo**, hoặc `python -m rideguard_sim` từ thư mục tool. Thư mục này chỉ gom các CLI đã có; không script nào tự chạy khi mở app hay import package.

## Các nhóm

- `testing/`: `smoke_pc_ai.py`, `steering_trace.py` kiểm PC/AI không mở COM. `hardware_acceptance.py`, `hardware_scenarios.py`, `hardware_imu_import.py`, `hardware_sample_warnings.py`, `accept_csv_flow.py`, `verify_imu_csv.py` là harness dùng Serial thật; chỉ chạy khi đã chọn đúng bo và đóng monitor khác.
- `hardware/`: `capture_hardware.py` thu logger; `compare_capture.py` đối chiếu file thu; `replay_session.py` phát lại phiên qua Serial.
- `data/`: `generate_imu_csv.py`, `make_synthetic_capture.py` tạo dữ liệu tổng hợp. Không tự sửa dữ liệu mẫu hiện có.
- `diagnostics/`: `measure_result_timing.py`, `probe_app_link.py`, `probe_lane_change.py` dùng COM; `probe_levels.py`, `probe_log_errors.py` đọc log; `probe_budget.py` tính ngân sách; `capture_ui.py` chụp giao diện.
- `firmware/`: giữ các tiện ích cũ `backup_firmware.py`, `backup_and_switch.py`, `flash_sensor_simulation.py`. Việc chuyển thư mục không kích hoạt backup/nạp. Hướng dẫn nạp tay vẫn ở [báo cáo firmware](../../../docs/simulator/reports/manual-firmware-2026-11-18.md); không dùng các tiện ích này như bước khởi chạy app.

## Lệnh thường dùng — PowerShell từ gốc repository

Chạy test tự động (không tự kết nối COM):

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONPATH=(Join-Path (Get-Location) 'tools/rideguard-simulator')
& tools/rideguard-simulator/.venv/Scripts/python.exe -X utf8 -m pytest tools/rideguard-simulator/tests -q -ra
```

Smoke bộ luật PC + model AI thật, thư mục output phải mới:

```powershell
& tools/rideguard-simulator/.venv/Scripts/python.exe -X utf8 tools/rideguard-simulator/scripts/testing/smoke_pc_ai.py --out .local/my-pc-ai-smoke
```

Từ **tools/rideguard-simulator**, cùng script có thể chạy trực tiếp hoặc dạng module:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/testing/smoke_pc_ai.py --out ../../.local/my-other-smoke
.venv/Scripts/python.exe -X utf8 -m scripts.data.generate_imu_csv --help
.venv/Scripts/python.exe -X utf8 scripts/hardware/capture_hardware.py --help
```

Đường dẫn input/output truyền qua tham số vẫn tính từ thư mục làm việc hiện tại. Các đường dẫn mẫu mặc định dựa trên `__file__` đã được điều chỉnh để tiếp tục trỏ đúng vị trí cũ. Các file `.py` trước đây ở gốc tool đã chuyển vào các nhóm trên; nếu có lệnh riêng ngoài repo, cần cập nhật đường dẫn tương ứng.

[Mục lục tài liệu](../../../docs/simulator/README.md) · [Hướng dẫn sử dụng](../../HUONG-DAN.md)
