#!/usr/bin/env python3
"""Replay a rebuilt EG image into the stock-hash-gated QEMU machine.

The nikon-z30-display machine refuses any ``-bios`` whose whole-file sha256 and
length are not the stock C1.20 EG image, so a rebuilt image cannot be passed
directly.  ``tools/probe_rom_write_qemu.py`` showed the gdbstub can still write
the ROM alias that holds the stock payload while the machine is paused at
reset.  This tool therefore boots the stock image, replays every differing byte
range plus the appended tail of the rebuilt image over RSP, verifies the
readback, and only then starts the live-view sequence.

QEMU display model only; not a camera test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'z30-qemu' / 'tools'))
sys.path.insert(0, str(ROOT / 'src'))

import run  # noqa: E402
from display_session import Session  # noqa: E402
from liveview_session import DEVICE_ENV, refresh, start  # noqa: E402

BYTE_OFFSET_BASE = 0x10020000
RSP_CHUNK = 1024
MERGE_GAP = 64
SAMPLES = (('band_top', 300, 30), ('band_bottom', 300, 450), ('photo_center', 300, 240),
           ('photo_left', 60, 240), ('cam_icon', 40, 150), ('info_icon', 690, 150),
           ('card_icon', 24, 330), ('bracket_left', 610, 455), ('bracket_right', 668, 455),
           ('top_edge', 300, 2), ('bottom_edge', 300, 478),
           ('left_bar', 20, 240), ('right_bar', 680, 240))


def ppm_pixels(ppm: bytes):
    magic, dimensions, maximum, pixels = ppm.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM')
    return width, height, pixels


def sample_grid(session) -> dict[str, list[int]]:
    width, _, pixels = ppm_pixels((session.graphics / 'gui-latest.ppm').read_bytes())
    result = {}
    for name, x, y in SAMPLES:
        index = (y * width + x) * 3
        result[name] = list(pixels[index:index + 3])
    return result


LAYER_POINTS = (('cam_icon', 40, 150), ('card_icon', 24, 330), ('info_icon', 690, 150),
                ('bracket_left', 610, 455), ('photo_center', 300, 240),
                ('left_bar', 20, 240), ('right_bar', 680, 240))


def layer_words(remote) -> dict[str, dict[str, list[int]]]:
    """Raw RGBA bytes at probe points of the image surface and the UI canvas.

    QEMU maps surface row ``r`` to screen row ``479 - r`` (the copy flips
    vertically), so the probe address uses ``row = 479 - y``.
    """
    surfaces = (('image', 0xD0959600), ('canvas', 0xD045F200))
    result: dict[str, dict[str, list[int]]] = {}
    for name, base in surfaces:
        values = {}
        for point, x, y in LAYER_POINTS:
            row = 479 - y
            address = base + (row * 720 + x) * 4
            values[point] = list(remote.memory(address, 4))
        result[name] = values
    return result


def rsp_write(remote, address: int, data: bytes) -> None:
    for offset in range(0, len(data), RSP_CHUNK):
        chunk = data[offset:offset + RSP_CHUNK]
        reply = remote.command(f'M{address + offset:x},{len(chunk):x}:{chunk.hex()}')
        if reply != 'OK':
            raise ValueError(f'RSP write {address + offset:#x}: {reply!r}')


def diff_ranges(stock: bytes, built: bytes) -> list[tuple[int, int]]:
    """Return half-open byte ranges where the rebuilt image differs from stock."""
    limit = min(len(stock), len(built))
    ranges: list[list[int]] = []
    for offset in range(limit):
        if stock[offset] != built[offset]:
            if ranges and offset - ranges[-1][1] <= MERGE_GAP:
                ranges[-1][1] = offset + 1
            else:
                ranges.append([offset, offset + 1])
    return [(start, end) for start, end in ranges]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--stock-firmware', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--mask-rects', default='',
                        help='Optional "x0,y0,x1,y1;..." state override (host-written, QEMU only)')
    parser.add_argument('--mask-alpha', type=int, default=48)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    soft = report['features']['aspect_profiles']['soft_mask']
    state_address = int(soft['state'], 16)
    stock = args.stock_firmware.read_bytes()
    built = args.firmware.read_bytes()
    if len(stock) != 33939514 or hashlib.sha256(stock).hexdigest() != report['source_sha256']:
        raise SystemExit('Stock image is not the build source')
    append_address = int(report['append_address'], 16)
    append_offset = append_address - BYTE_OFFSET_BASE
    if not 0 <= append_offset < len(stock):
        raise SystemExit('Appended region does not start inside the payload')
    tail = built[append_offset:]
    tail_address = append_address
    if len(tail) < report['append_length']:
        raise SystemExit('Appended tail is shorter than the build report')
    ranges = diff_ranges(stock[:append_offset], built[:append_offset])

    prefix = (args.output_dir / 'session').resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    session = Session(str(prefix), firmware_image=args.stock_firmware.resolve(), device_env=DEVICE_ENV)
    context: dict[str, object] = dict(report=str(args.report),
                                      build_sha=hashlib.sha256(built).hexdigest(),
                                      build_size=len(built),
                                      stock_size=len(stock),
                                      tail_address=hex(tail_address), tail_length=len(tail),
                                      edit_ranges=len(ranges),
                                      state=hex(state_address), samples={}, layers={},
                                      readback={})
    try:
        remote = session.remote
        for begin, end in ranges:
            rsp_write(remote, BYTE_OFFSET_BASE + begin, built[begin:end])
        rsp_write(remote, tail_address, tail)
        mismatched = 0
        for begin, end in ranges:
            if remote.memory(BYTE_OFFSET_BASE + begin, end - begin) != built[begin:end]:
                mismatched += 1
        if remote.memory(tail_address, len(tail)) != tail:
            mismatched += 1
        context['readback'] = dict(mismatched=mismatched, ok=mismatched == 0)
        if mismatched:
            raise RuntimeError('Replay readback mismatch')
        context['submit_site'] = remote.memory(0x1055D112, 10).hex()

        # Optional host-written geometry override: exercises the same fill code
        # with other geometries (side bars, narrower bands) without needing the
        # dynamic projection hook to be reachable in the QEMU fixture.  The
        # phase writer below fills the whole state block on every transition.
        if args.mask_rects:
            context['mask_rects'] = [[int(value) for value in chunk.split(',')]
                                     for chunk in args.mask_rects.split(';') if chunk.strip()]

        # Start inactive.  The trial state ships active, but during early boot
        # the display slots do not yet describe the 720x480 surfaces, so
        # activate only after the live-view pipeline is up, the same way the
        # dynamic path enables the mask from the grid hook.
        rsp_write(remote, state_address, struct.pack('<I', 0))
        # Phase state is a whole 128-byte block: the fill hook reads only the
        # count/rects/color words, so every phase writes all of them.
        rects = [tuple(rect) for rect in soft.get('trial_rects', [])]
        if args.mask_rects:
            rects = [tuple(int(value) for value in chunk.split(','))
                     for chunk in args.mask_rects.split(';') if chunk.strip()]

        def phase_state(count: int, color: int) -> bytes:
            state = bytearray(128)
            struct.pack_into('<I', state, 0x08, args.mask_alpha)
            struct.pack_into('<I', state, 0x0C, count)
            for index, rect in enumerate(rects[:4]):
                struct.pack_into('<4I', state, 0x10 + index * 16, *rect)
            struct.pack_into('<I', state, 0x50, color)
            return bytes(state)

        # Start inactive: zero rectangles mean the fill hook writes nothing.
        rsp_write(remote, state_address, phase_state(0, args.mask_alpha << 24))
        start(session)
        refresh(session)
        session.run_for(0.3)
        session.frame('00-trial-inactive')
        context['samples']['00-trial-inactive'] = sample_grid(session)
        context['layers']['00-trial-inactive'] = layer_words(remote)

        rsp_write(remote, state_address, phase_state(len(rects), args.mask_alpha << 24))
        refresh(session)
        session.run_for(0.4)
        session.frame('01-trial-active')
        context['samples']['01-trial-active'] = sample_grid(session)
        context['layers']['01-trial-active'] = layer_words(remote)

        # Erase: one transparent SRC fill blanks the bars; the display pipeline
        # rewrites the image surface afterwards, and the fill's first/last word
        # compare then skips the already-zeroed rows.
        rsp_write(remote, state_address, phase_state(len(rects), 0))
        refresh(session)
        session.run_for(0.3)
        session.frame('02-trial-erased')
        context['samples']['02-trial-erased'] = sample_grid(session)
        context['layers']['02-trial-erased'] = layer_words(remote)
    finally:
        session.close()

    (args.output_dir / 'context.json').write_text(json.dumps(context, indent=2), encoding='utf-8')
    print(json.dumps(context['samples'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
