# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""QEMU end-to-end check of soft-aspect-mask EG trials.

Loads build outputs themselves as the QEMU firmware image (the stock validator
is bypassed locally because the appended module changes size/hash). A trial with
active state must dim only the bands outside the sample photo; a trial with an
erase-pending state must show the stock baseline. QEMU display model only.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent/'z30-qemu'/'tools'))
sys.path.insert(0, str(ROOT/'src'))

import run  # noqa: E402
run.firmware = lambda path, host_version=None: Path(path).resolve()  # research-only load
from display_session import Session  # noqa: E402
from liveview_session import DEVICE_ENV, refresh, start  # noqa: E402

BYTE_OFFSET_BASE = 0x10020000
SAMPLES = (('band_top', 300, 30), ('band_bottom', 300, 450), ('photo_center', 300, 240),
           ('photo_left', 60, 240), ('cam_icon', 40, 150), ('info_icon', 690, 150),
           ('card_icon', 24, 330), ('bracket_left', 610, 455), ('bracket_right', 668, 455),
           ('top_edge', 300, 2), ('bottom_edge', 300, 478))


def ppm_pixels(ppm):
    magic, dimensions, maximum, pixels = ppm.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width*height*3:
        raise ValueError('Invalid PPM')
    return width, height, pixels


def sample_grid(session):
    width, height, pixels = ppm_pixels((session.graphics/'gui-latest.ppm').read_bytes())
    result = {}
    for name, x, y in SAMPLES:
        index = (y*width + x)*3
        result[name] = list(pixels[index:index+3])
    return result


def read_words(remote, address, count):
    data = remote.memory(address, count*4)
    return list(struct.unpack('<' + 'I'*count, data))


def variant_state(firmware, state_address, flags):
    """Copy a trial image and set the boot-time mask state flags word."""
    offset = state_address - BYTE_OFFSET_BASE
    data = bytearray(firmware.read_bytes())
    if not 0 <= offset <= len(data) - 4:
        raise ValueError('Mask state address outside the firmware image')
    struct.pack_into('<I', data, offset, flags)
    return bytes(data)


def run_case(label, firmware, output_dir, state_address=None):
    prefix = (output_dir/label).resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    session = Session(str(prefix), firmware_image=firmware, device_env=DEVICE_ENV)
    try:
        start(session)
        refresh(session)
        session.run_for(0.4)
        session.frame('01-liveview')
        samples = sample_grid(session)
        state = read_words(session.remote, state_address, 8) if state_address else None
    finally:
        session.close()
    return samples, state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path, required=True, help='EG209020.json from the trial build')
    p.add_argument('--firmware', type=Path, required=True, help='EG209020.BIN from the trial build')
    p.add_argument('--stock-firmware', type=Path, required=True, help='Unmodified stock .bi for the baseline')
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    report = json.loads(a.report.read_text(encoding='utf-8'))
    soft = report['features']['aspect_profiles']['soft_mask']
    state_address = int(soft['state'], 16)
    firmware = a.firmware.read_bytes()

    variants = {}
    for label, flags in (('02-trial-active', 1), ('03-trial-erase', 2)):
        directory = a.output_dir/label
        directory.mkdir(parents=True, exist_ok=True)
        data = variant_state(a.firmware, state_address, flags)
        path = directory/'EG209020.BIN'
        path.write_bytes(data)
        variants[label] = (path, flags, hashlib.sha256(data).hexdigest())

    context = dict(report=str(a.report), build_sha=hashlib.sha256(firmware).hexdigest(),
                   build_size=len(firmware), state=hex(state_address), soft_mask=soft, cases={})
    samples, _ = run_case('01-stock', a.stock_firmware.resolve(), a.output_dir)
    context['cases']['01-stock'] = dict(samples=samples)
    for label, (path, flags, sha) in variants.items():
        samples, state = run_case(label, path, a.output_dir, state_address)
        context['cases'][label] = dict(firmware_sha=sha, boot_flags=flags,
                                       state_words=[hex(v) for v in state], samples=samples)
    (a.output_dir/'context.json').write_text(json.dumps(context, indent=2), encoding='utf-8')
    print(json.dumps(context['cases'], indent=2))


if __name__ == '__main__':
    main()
