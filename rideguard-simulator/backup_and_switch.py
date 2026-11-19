"""ESP32 local operator tool: verified full backup before optional simulation flash.

Uses the locally installed PlatformIO esptool 4.x; hold BOOT when starting.
Does not erase the whole chip, change eFuses, or overwrite an existing backup.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', required=True)
    ap.add_argument('--flash', action='store_true', help='Flash sensor_simulation AFTER backup verification')
    ap.add_argument('--resume', type=Path, help='Resume an unverified .partial directory after checking board and prefix digest')
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    packages = Path.home()/'.platformio/packages'
    sys.path.insert(0, str(packages/'tool-esptoolpy'))
    import esptool
    import serial
    from serial.tools import list_ports
    from esptool.targets.esp32 import ESP32ROM
    from esptool.util import flash_size_bytes

    port = next((p for p in list_ports.comports() if p.device == args.port), None)
    if port is None:
        raise RuntimeError('Selected port is not present')
    stamp = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d-%H%M%S%z')
    directory = args.resume.resolve() if args.resume else root/'.local'/f'logger-backup-{stamp}-{uuid.uuid4().hex[:8]}'
    if not args.resume:
        directory.mkdir(parents=True, exist_ok=False)
    backup = directory/'full-flash.bin'
    build = root/'firmware/.pio/build/sensor_simulation'
    images = [(0x1000, build/'bootloader.bin'), (0x8000, build/'partitions.bin'),
              (0xe000, packages/'framework-arduinoespressif32/tools/partitions/boot_app0.bin'),
              (0x10000, build/'firmware.bin')]
    if args.flash and not all(p.is_file() for _, p in images):
        raise RuntimeError('Build sensor_simulation before running this tool')
    meta = dict(timestamp=stamp, port=port.device, usb=port.hwid,
                esptool_version=esptool.__version__, backup_verified=False, flashed=False)
    previous = None
    if args.resume:
        previous = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        if previous.get('backup_verified') or previous.get('flashed') or backup.exists():
            raise RuntimeError('Refusing to overwrite a completed backup')
        meta.update(previous)
        meta['resumed_at'] = stamp
    def save():
        (directory/'manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    save()
    print(f'Backup directory: {directory}', flush=True)
    sock = serial.Serial(port=None, baudrate=115200, timeout=3)
    sock.dtr = sock.rts = False
    sock.port = args.port
    try:
        sock.open()
        if sys.platform == 'win32':
            # Flash stub streams multiple 4 KiB blocks ahead of acknowledgments.
            # The Windows default RX queue is too small for that burst.
            sock.set_buffer_size(rx_size=1024*1024, tx_size=65536)
        # This CP210x board's RTS pulse resets EN; operator holds GPIO0/BOOT.
        sock.rts = True; time.sleep(.15); sock.rts = False; time.sleep(.4)
        esp = ESP32ROM(sock)
        esp.connect(mode='no_reset', attempts=3)
        if esp.get_flash_encryption_enabled() or esp.get_secure_boot_enabled():
            raise RuntimeError('Security-enabled board: stop; generic raw restore is not assumed safe')
        meta.update(chip=esp.get_chip_description(), mac=':'.join(f'{b:02x}' for b in esp.read_mac()))
        if previous and previous['mac'] != meta['mac']:
            raise RuntimeError('Resume board MAC does not match')
        # ROM command/response avoids the stub's long unsolicited data stream.
        esp.flash_spi_attach(0)
        flash_id = esp.flash_id()
        size_name = esptool.DETECTED_FLASH_SIZES.get(flash_id >> 16)
        if size_name is None:
            raise RuntimeError(f'Unknown flash ID {flash_id:x}; refusing guessed backup size')
        size = flash_size_bytes(size_name)
        esp.flash_set_parameters(size)
        meta.update(flash_id=hex(flash_id), flash_size=size_name, flash_bytes=size)
        save()
        print(f'Reading ALL flash: {size} bytes, {meta["chip"]}', flush=True)
        last = -1
        def progress(done, total):
            nonlocal last
            percent = int(done*100/total)//10
            if percent != last:
                print(f'Backup read {percent*10}%', flush=True); last = percent
        # ROM reads 64 bytes per acknowledged request; verify the entire image
        # against device MD5 before allowing any persistent write.
        partial_path = directory/'read-in-progress.partial'
        prefix = partial_path.read_bytes() if args.resume else b''
        if len(prefix)>size or len(prefix)%4096:
            raise RuntimeError('Resume file must contain complete sectors within flash size')
        if prefix and hashlib.md5(prefix).hexdigest() != esp.flash_md5sum(0,len(prefix)):
            raise RuntimeError('Resume prefix does not match current board flash')
        blocks = [prefix]
        with partial_path.open('ab' if args.resume else 'xb') as partial:
            for offset in range(len(prefix), size, 4096):
                for attempt in range(5):
                    try:
                        block = esp.read_flash(offset, min(4096, size-offset))
                        break
                    except esptool.FatalError as error:
                        print(f'Read retry offset={offset:#x} attempt={attempt+1}: {error}', flush=True)
                        if attempt == 4:
                            raise
                        sock.reset_input_buffer()
                        esp._slip_reader = esptool.loader.slip_reader(sock, esp.trace)
                        try:
                            esp.sync()
                            esp.flash_spi_attach(0)
                            esp.flash_set_parameters(size)
                        except esptool.FatalError as reconnect_error:
                            print(f'Resync failed: {reconnect_error}', flush=True)
                blocks.append(block)
                partial.write(block); partial.flush()
                progress(offset+len(block), size)
        data = b''.join(blocks)
        if len(data) != size:
            raise RuntimeError('Incomplete flash read; no write allowed')
        with backup.open('xb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        disk = backup.read_bytes()
        device_md5 = esp.flash_md5sum(0, size)
        if disk != data or hashlib.md5(disk).hexdigest() != device_md5:
            raise RuntimeError('Full backup does not match device digest; no write allowed')
        meta.update(backup_verified=True, sha256=hashlib.sha256(disk).hexdigest(),
                    device_md5=device_md5, backup_file=str(backup))
        (directory/'SHA256.txt').write_text(f'{meta["sha256"]}  full-flash.bin\n', encoding='ascii')
        restore = (f'Full flash restore, SAME board. Verify SHA256.txt before writing.\n'
                   f'Hold BOOT/reset to enter bootloader; close Serial monitors.\n'
                   f'From repository root, PowerShell (update port after discovery):\n'
                   f'& firmware/.venv/Scripts/python.exe "{packages / "tool-esptoolpy/esptool.py"}" '
                   f'--chip esp32 --port {args.port} --baud 115200 --before no_reset '
                   f'--after hard_reset write_flash --flash_mode keep --flash_freq keep '
                   f'--flash_size keep 0x0 "{backup}"\n'
                   'Require successful flash hash verification, release BOOT, press EN.\n'
                   'Logger previously observed at 460800 baud. Restore is not executed by this tool.\n')
        (directory/'RESTORE.txt').write_text(restore, encoding='utf-8')
        save()
        print(f'BACKUP VERIFIED: {size} bytes; SHA256 {meta["sha256"]}', flush=True)
        if args.flash:
            esp = esp.run_stub()
            meta['images'] = [dict(offset=hex(a), path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for a,p in images]
            save()
            argv = ['--chip','esp32','--baud','115200','--before','no_reset','--after','no_reset_stub',
                    'write_flash','--flash_mode','dio','--flash_freq','40m','--flash_size',size_name]
            for address, path in images:
                argv.extend([hex(address), str(path)])
            esptool.main(argv, esp=esp)
            meta['flashed'] = True; save()
            print('FLASH VERIFIED. Release BOOT and press EN/RESET.', flush=True)
        else:
            print('Backup only. No flash written. Release BOOT and press EN/RESET.', flush=True)
    finally:
        sock.close()


if __name__ == '__main__':
    main()
