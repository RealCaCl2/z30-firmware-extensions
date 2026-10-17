"""Verify coordinated XPAN research geometry using original ARM instructions.

This exercises synthetic configuration only, without capture hardware or JPEG
encoding. Unsupported branch results are saved explicitly, not counted as a
completed feature.
"""
import argparse
import binascii
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, sha256
from build_xpan_trial import build_trial, CHANGES


def verify(stock, candidate):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_MEM_READ
    from unicorn.arm_const import UC_ARM_REG_PC

    report = {'stock_sha256': sha256(stock), 'candidate_sha256': sha256(candidate),
              'cases': [], 'unsupported_branch_observations': [],
              'status': 'emulator_geometry_only', 'camera_verified': False}
    assert len(stock) == len(candidate)
    assert binascii.crc_hqx(candidate, 0) == 0
    allowed = {len(stock) - 2, len(stock) - 1}
    for address, old, new, _ in CHANGES:
        offset = address - BASE
        assert stock[offset:offset + 2] == struct.pack('<H', old)
        assert candidate[offset:offset + 2] == struct.pack('<H', new)
        allowed.update((offset, offset + 1))
    actual = {i for i, (a, b) in enumerate(zip(stock, candidate)) if a != b}
    assert actual <= allowed
    report['changed_byte_count'] = len(actual)
    baseline = {}
    for name, image in (('stock', stock), ('coordinated', candidate)):
        c = Context(stock, image)
        reads = []

        def read(u, access, address, size, value, extra):
            if 0x11570000 <= address < 0x115B0000:
                reads.append({'pc': hex(u.reg_read(UC_ARM_REG_PC)), 'address': hex(address),
                              'bytes': bytes(u.mem_read(address, size)).hex()})

        c.u.hook_add(UC_HOOK_MEM_READ, read)
        obj, info, holder, header = RAM, RAM + 0x1000, RAM + 0x5000, RAM + 0x4000

        def reset(area, size, alternate=False):
            c.u.mem_write(obj, bytes(0x100))
            c.u.mem_write(info, bytes(0x3000))
            c.u.mem_write(obj + 4, struct.pack('<I', holder))
            c.u.mem_write(holder, struct.pack('<I', header))
            c.u.mem_write(info + 0x208, bytes([area]))
            c.u.mem_write(info + 0x229, bytes([size]))
            if alternate:
                c.u.mem_write(info + 0x104, struct.pack('<I', 1))
            reads.clear()

        for area in (0, 2, 4):
            for size in range(3):
                reset(area, size)
                c.call(0x10142BEA, (obj, info))
                buffer = bytes(c.u.mem_read(obj, 44))
                buffer_reads = list(reads)
                reset(area, size)
                c.call(0x1014237A, (obj, info, 0))
                config = bytes(c.u.mem_read(obj, 0x70))
                config_reads = list(reads)
                reset(area, size)
                # Native descriptor helper receives info+0x208 as its input.
                c.u.mem_write(info, bytes([area]))
                c.u.mem_write(info + 0x22, bytes([size]))
                c.call(0x101D5B36, (info, obj))
                descriptor = bytes(c.u.mem_read(obj, 28))
                item = {'image': name, 'area': area, 'size': size,
                        'buffer_hex': buffer.hex(), 'config_hex': config.hex(),
                        'descriptor_hex': descriptor.hex(),
                        'output_wh': struct.unpack_from('<2H', buffer, 0x14),
                        'padded_wh': struct.unpack_from('<2H', buffer, 0x10),
                        'crop_wh_candidate': struct.unpack_from('<2H', config, 0x22),
                        'crop_xy_candidate': struct.unpack_from('<2H', config, 0x26),
                        'descriptor_u16': struct.unpack('<14H', descriptor),
                        'buffer_reads': buffer_reads, 'config_reads': config_reads,
                        'descriptor_reads': list(reads)}
                if name == 'stock':
                    baseline[area, size] = item
                elif area != 2:
                    before = baseline[area, size]
                    for key in ('buffer_hex', 'config_hex', 'descriptor_hex'):
                        assert item[key] == before[key]
                else:
                    assert item['output_wh'] == ((5568, 2056), (4176, 1544), (2784, 1024))[size]
                    assert item['crop_wh_candidate'] == (5568, 2056)
                    assert item['crop_xy_candidate'] == (16, 836)
                    assert 836 * 2 + 2056 == 3728
                    assert 828 * 2 + 2072 == 3728
                    assert item['descriptor_u16'][:6] == (5600, 3728, 5600, 2072, 0, 828)
                    if size == 0:
                        d = item['descriptor_u16']
                        assert (d[10] - d[8], d[11] - d[9]) == (5568, 2056)
                    else:
                        assert item['descriptor_u16'][12:] == item['output_wh']
                assert all(a >= b for a, b in zip(item['padded_wh'], item['output_wh']))
                report['cases'].append(item)
        reset(2, 0, alternate=True)
        c.call(0x1014237A, (obj, info, 0))
        config = bytes(c.u.mem_read(obj, 0x70))
        report['unsupported_branch_observations'].append({
            'image': name, 'synthetic_input': {'area': 2, 'size': 0, 'info_104_u32': 1},
            'crop_wh_candidate': struct.unpack_from('<2H', config, 0x22),
            'crop_xy_candidate': struct.unpack_from('<2H', config, 0x26),
            'config_hex': config.hex(), 'table_reads': list(reads)})
    report['passed_cases'] = len(report['cases'])
    report['limitations'] = [
        'Original instructions with synthetic initialized objects, no live capture context.',
        'Field names are hypotheses corroborated by read/write data flow, not ISP submission proof.',
        'Alternate info+0x104 branch remains unsupported; this global-table patch must not be installed.',
        'No new menu item, live-view mask, JPEG bytes, NEF metadata, camera files or hardware verification.',
    ]
    report['source_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (Path(__file__), Path(__file__).with_name('build_xpan_trial.py'),
                                         Path(__file__).with_name('native_runtime.py'))}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--input', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/research/xpan-trial')
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    stock = extract_eg(args.input.read_bytes())
    candidate, patch = build_trial(stock)
    report = verify(stock, candidate)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'native-verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Passed {report["passed_cases"]} geometry cases; alternate branch explicitly unsupported.')
