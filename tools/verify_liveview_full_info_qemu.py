"""Guest-rendered DISP checks using the existing assisted live-view fixture.

The preview is a diagnostic pattern, not sensor output. Patches are loaded into
the QEMU process only; the stock firmware and physical camera are not written.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import DEVICE_ENV, start, refresh
    image = a.image.read_bytes()
    report = json.loads(a.report.read_text())
    assert hashlib.sha256(image).hexdigest() == report['sha256']
    s = Session(str(a.output_dir.resolve()), device_env=DEVICE_ENV)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            block = data[offset:offset + 1024]
            assert s.remote.command(f'M{address + offset:x},{len(block):x}:' + block.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data

    def disp():
        return s.remote.memory(0xC2016A68 + 0x196, 1)[0]

    try:
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            expected = bytes.fromhex(edit['expected_hex'])
            assert s.remote.memory(address, len(expected)) == expected
            write(address, bytes.fromhex(edit['replacement_hex']))
        stock_size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + stock_size - 2, image[stock_size - 2:])
        start(s)
        # Assisted boot's initialized-RAM preload is separate from patched ROM.
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            if 0x11F81C94 <= address < 0x11F81C94 + 0xF8258:
                write(0xC1800000 + address - 0x11F81C94,
                      bytes.fromhex(edit['replacement_hex']))
        s.audit.append(dict(candidate_sha256=report['sha256'], sensor_input='diagnostic pattern'))
        # Native DISP setter, refresh, and cycle; do not assign the snapshot.
        s.input(0x10369128, [0])
        for index, expected in enumerate((0, 1, 2, 3, 5, 4, 0)):
            refresh(s)
            s.run_for(.5)
            observed = disp()
            s.audit.append(dict(expected_disp=expected, observed_disp=observed))
            assert observed == expected, (expected, observed)
            print(s.frame(f'{index:02d}-disp-{expected}'), flush=True)
            result = s.input(0x10372528, [])
            assert result['completed'] and result['context_restored']
        s.write_audit()
    finally:
        s.close()


if __name__ == '__main__':
    main()
