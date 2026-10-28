"""Build a stock-only experimental 2.4 GHz power-backoff trial EG module.

Changes four NVRAM rate-backoff fields by 0.5 or 1 dB, without changing board
maximum, PA calibration, country or power override bits. Does not flash a camera.
"""
import argparse
import binascii
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from probe_wireless_config import SOURCE, DESTINATION, SIZE, runtime

NVRAM_ROM, NVRAM_SIZE = 0x11E90059, 0x898
FIELDS = {
    'cckbw202gpo': '0x8888',
    'ofdmlrbw202gpo': '0x00aa',
    'dot11agofdmhrbw202gpo': '0xaaaa',
    'mcsbw202gpo': '0xaaaaaaaa',
}


def callback_view(image):
    """Run the original ARM callback, with the image copied to its RAM address."""
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR, UC_ARM_REG_PC
    u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
    u.mem_write(DESTINATION, image[SOURCE - BASE:SOURCE - BASE + SIZE])
    u.mem_map(0x50000000, 0x1000)
    u.mem_map(0x60000000, 0x1000)
    u.reg_write(UC_ARM_REG_R0, 0x50000000)
    u.reg_write(UC_ARM_REG_R1, 0x50000004)
    u.reg_write(UC_ARM_REG_LR, 0x60000000)
    u.emu_start(runtime(0x11D62DAC), 0x60000000, count=20)
    if u.reg_read(UC_ARM_REG_PC) != 0x60000000:
        raise ValueError('NVRAM callback failed to return')
    pointer, length = struct.unpack('<2I', u.mem_read(0x50000000, 8))
    if (pointer, length) != (runtime(NVRAM_ROM), NVRAM_SIZE):
        raise ValueError('Unexpected NVRAM callback mapping')
    return bytes(u.mem_read(pointer, length))


def build_trial(stock, relax_db):
    StockImage(stock)
    steps = {0.5: 1, 1.0: 2}[relax_db]
    original = callback_view(stock)
    plan = Plan(stock)
    changes = []
    for key, expected in FIELDS.items():
        needle = (key + '=' + expected).encode('ascii') + b'\0'
        if original.count(needle) != 1:
            raise ValueError('Unexpected stock board field: ' + key)
        # Each nibble is a half-dB rate backoff. Zero entries remain zero.
        digits = ''.join(format(max(0, int(c, 16) - steps), 'x') for c in expected[2:])
        replacement = '0x' + digits
        offset = original.index(needle) + len(key) + 1
        plan.edit(NVRAM_ROM + offset, replacement.encode('ascii'),
                  'Experimental 2.4 GHz rate backoff: ' + key, expected.encode('ascii'))
        changes.append({'key': key, 'before': expected, 'after': replacement})
    candidate, report = plan.finish()
    changed = callback_view(candidate)
    if len(candidate) != len(stock) or len(changed) != len(original):
        raise ValueError('Trial unexpectedly changed image or NVRAM length')
    expected_view = original
    for entry in changes:
        before = (entry['key'] + '=' + entry['before']).encode('ascii') + b'\0'
        after = (entry['key'] + '=' + entry['after']).encode('ascii') + b'\0'
        expected_view = expected_view.replace(before, after)
    if changed != expected_view or binascii.crc_hqx(candidate, 0):
        raise ValueError('Trial readback or EG CRC check failed')
    permitted = {len(stock) - 2, len(stock) - 1}
    for edit in plan.edits:
        permitted.update(range(edit.offset, edit.offset + len(edit.expected)))
    differences = {i for i, (a, b) in enumerate(zip(stock, candidate)) if a != b}
    if not differences <= permitted:
        raise ValueError('Unexpected byte changes outside the selected fields and EG CRC')
    report.update(
        trial='wifi-2g-power-backoff', intended_backoff_reduction_db=relax_db,
        status='offline_candidate_not_hardware_validated', changes=changes,
        nvram_callback_rom='0x11d62dac', nvram_rom=hex(NVRAM_ROM), nvram_size=NVRAM_SIZE,
        nvram_callback_readback_verified=True, changed_byte_count=len(differences),
        source_sha256=sha256(stock), candidate_sha256=sha256(candidate),
        limitations=[
            'Standalone stock-relative EG module, not a complete Nikon update container.',
            'No measured EIRP, rate-specific output, driver acceptance or throughput gain.',
            'Board maximum, PA calibration, country and other limits still apply.',
            'NVRAM callback readback does not prove the full radio upload or live use.',
            'Units are corroborated by related Infineon SROM/NVRAM documentation.',
        ])
    return candidate, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--relax-db', type=float, choices=(0.5, 1.0), default=1.0)
    args = parser.parse_args()
    report_path = args.output.with_suffix('.json')
    if args.output.exists() or report_path.exists():
        parser.error('Output and report must both be new files')
    candidate, report = build_trial(extract_eg(args.source.read_bytes()), args.relax_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(candidate)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Built offline trial: {report['changed_byte_count']} changed bytes; "
          'stock ARM NVRAM callback readback and EG CRC passed')


if __name__ == '__main__':
    main()
