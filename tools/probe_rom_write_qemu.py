#!/usr/bin/env python3
"""Check whether QEMU's gdbstub can write the -bios ROM region.

The z30 QEMU machine hash-gates the -bios file, so a rebuilt EG image cannot
be passed directly.  If the gdbstub can still write the ROM alias that holds
the stock payload, the build's edits plus appended tail could be replayed
into a stock boot, giving a true end-to-end run without touching QEMU.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_qemu_tools(qemu_root: Path):
    sys.path.insert(0, str(qemu_root / 'tools'))
    import display_session  # noqa: E402
    import run  # noqa: E402

    return display_session, run


def rsp_write(remote, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 1024):
        chunk = data[offset:offset + 1024]
        reply = remote.command(f'M{address + offset:x},{len(chunk):x}:{chunk.hex()}')
        if reply != 'OK':
            raise ValueError(f'RSP write {address + offset:#x}: {reply!r}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--qemu-root', type=Path, default=ROOT / '..' / 'z30-qemu')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build' / 'research' / 'rom-write')
    args = parser.parse_args()

    qemu_root = args.qemu_root.resolve()
    display_session, run = load_qemu_tools(qemu_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    stock = (qemu_root / run.DEFAULT_FIRMWARE)
    session = display_session.Session(
        str(args.output_dir / 'session'),
        firmware_image=stock,
        device_env={'Z30_MMIO_QUIET': '1'},
    )
    try:
        remote = session.remote
        report: dict[str, object] = {'stock_size': stock.stat().st_size}
        for label, address in (
            ('rom_submit_site', 0x1055D112),
            ('rom_tail_preview', 0x1205E03A),
            ('ram_control', 0xC6E10000),
        ):
            before = remote.memory(address, 16)
            payload = hashlib.sha256(label.encode()).digest() * 2
            try:
                rsp_write(remote, address, payload)
                error = None
            except Exception as exc:  # noqa: BLE001 - diagnostic probe
                error = type(exc).__name__ + ': ' + str(exc)
            after = remote.memory(address, 16)
            report[label] = {
                'address': hex(address),
                'before': before.hex(),
                'after': after.hex(),
                'written': payload.hex(),
                'write_error': error,
                'matches': after == payload,
            }
        # Confirm a stock code word still decodes where expected.
        site = remote.memory(0x1055D112, 10).hex()
        report['submit_site_now'] = site
        report['submit_site_expected'] = '70b50c46054625f031f8'
    finally:
        session.close()

    dumped = json.dumps(report, indent=2)
    (args.output_dir / 'rom-write.json').write_text(dumped + '\n')
    print(dumped)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
