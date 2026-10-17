"""Run native geometry verification for all six independent aspect trials."""
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
from build_aspect_trials import PROFILES, build_trial


def measure(stock, image, context=None):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_MEM_READ
    from unicorn.arm_const import UC_ARM_REG_PC
    c = context if context is not None else Context(stock, image)
    reads = []

    def read(u, access, address, size, value, extra):
        if 0x11570000 <= address < 0x115B0000:
            reads.append({'pc': hex(u.reg_read(UC_ARM_REG_PC)), 'address': hex(address),
                          'bytes': bytes(u.mem_read(address, size)).hex()})

    c.u.hook_add(UC_HOOK_MEM_READ, read)
    obj, info, holder, header = RAM, RAM + 0x1000, RAM + 0x5000, RAM + 0x4000

    def reset(area, size):
        c.u.mem_write(obj, bytes(256))
        c.u.mem_write(info, bytes(0x3000))
        c.u.mem_write(obj + 4, struct.pack('<I', holder))
        c.u.mem_write(holder, struct.pack('<I', header))
        c.u.mem_write(info + 0x208, bytes([area]))
        c.u.mem_write(info + 0x229, bytes([size]))
        reads.clear()

    cases = []
    for area in (0, 2, 4):
        for size in range(3):
            reset(area, size)
            c.call(0x10142BEA, (obj, info))
            buffer = bytes(c.u.mem_read(obj, 44))
            reset(area, size)
            c.call(0x1014237A, (obj, info, 0))
            config = bytes(c.u.mem_read(obj, 0x70))
            config_reads = list(reads)
            reset(area, size)
            c.u.mem_write(info, bytes([area]))
            c.u.mem_write(info + 0x22, bytes([size]))
            c.call(0x101D5B36, (info, obj))
            descriptor = bytes(c.u.mem_read(obj, 28))
            internal = c.call(0x1020BFC8, (0, area, 0))
            cases.append({'area': area, 'size': size,
                          'output_wh': struct.unpack_from('<2H', buffer, 0x14),
                          'padded_wh': struct.unpack_from('<2H', buffer, 0x10),
                          'config_parent_wh': struct.unpack_from('<2H', config, 0x22),
                          'config_origin_xy': struct.unpack_from('<2H', config, 0x26),
                          'config_trim_xy': struct.unpack_from('<2H', config, 0x38),
                          'config_trimmed_wh': struct.unpack_from('<2H', config, 0x3C),
                          'config_output_wh': struct.unpack_from('<2H', config, 0x56),
                          'internal_selector_wh': struct.unpack('<2H', c.u.mem_read(internal, 4)),
                          'descriptor_u16': struct.unpack('<14H', descriptor),
                          'buffer_hex': buffer.hex(), 'config_hex': config.hex(),
                          'descriptor_hex': descriptor.hex(), 'config_reads': config_reads})
    return cases


def verify(stock, output):
    baseline = measure(stock, stock)
    result = {'source_sha256': sha256(stock), 'baseline_cases': baseline, 'profiles': {},
              'status': 'native_geometry_verified_not_capture_verified',
              'menu_entries_added': False, 'camera_verified': False,
              'limitations': ['Independent area-2 experiments, not simultaneous menu choices.',
                              'Synthetic normal-photo inputs; no ISP, JPEG bytes, SD files or live view.',
                              'RAW/alternate branches and global table consumers remain unvalidated.']}
    output.mkdir(parents=True, exist_ok=True)
    for profile, ratio in PROFILES.items():
        image, patch = build_trial(stock, profile)
        assert len(image) == len(stock) and binascii.crc_hqx(image, 0) == 0
        allowed = {len(stock) - 2, len(stock) - 1}
        for e in patch['edits']:
            offset = int(e['offset'], 16)
            expected, replacement = bytes.fromhex(e['expected_hex']), bytes.fromhex(e['replacement_hex'])
            assert stock[offset:offset + len(expected)] == expected
            assert image[offset:offset + len(replacement)] == replacement
            allowed.update(range(offset, offset + len(expected)))
        differences = {i for i, (a, b) in enumerate(zip(stock, image)) if a != b}
        assert differences <= allowed
        cases = measure(stock, image)
        x, y, width, height = patch['target_large_in_dx_xywh']
        for before, case in zip(baseline, cases):
            assert all(a >= b for a, b in zip(case['padded_wh'], case['output_wh']))
            if case['area'] != 2:
                for field in ('buffer_hex', 'config_hex', 'descriptor_hex', 'internal_selector_wh'):
                    assert case[field] == before[field], (profile, case['area'], field)
                continue
            size = case['size']
            assert case['output_wh'] == tuple(patch['target_jpeg_wh'][size])
            assert case['config_output_wh'] == case['output_wh']
            assert case['config_trimmed_wh'] == (width, height)
            origin = case['config_origin_xy']
            trim = case['config_trim_xy']
            assert (origin[0] + trim[0], origin[1] + trim[1]) == (16 + x, 8 + y)
            assert 2 * x + width == 5568 and 2 * y + height == 3712
            assert case['internal_selector_wh'] == (width + 32, height + 16)
            d = case['descriptor_u16']
            assert d[:6] == (5600, 3728, width + 32, height + 16, x, y)
            assert all(0 <= d[i] < d[i + 2] <= d[i - 6] for i in (8, 9))
            if size == 0:
                assert (d[10] - d[8], d[11] - d[9]) == (width, height)
            else:
                assert d[12:] == case['output_wh']
        folder = output / profile
        folder.mkdir(exist_ok=True)
        (folder / 'research.eg.bin').write_bytes(image)
        (folder / 'research.json').write_text(json.dumps(patch, indent=2) + '\n', encoding='utf-8')
        result['profiles'][profile] = {'ratio': ratio, 'target_jpeg_wh': patch['target_jpeg_wh'],
                                       'large_xywh': [x, y, width, height],
                                       'candidate_sha256': sha256(image), 'changed_bytes': len(differences),
                                       'ratio_error_percent': [(w * ratio[1] / (h * ratio[0]) - 1) * 100
                                                               for w, h in patch['target_jpeg_wh']],
                                       'cases': cases, 'passed_cases': len(cases)}
        print(f'{profile}: passed {len(cases)} native geometry cases')
    result['passed_cases'] = len(baseline) + sum(x['passed_cases'] for x in result['profiles'].values())
    result['source_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                               (Path(__file__), Path(__file__).with_name('build_aspect_trials.py'),
                                Path(__file__).with_name('native_runtime.py'))}
    (output / 'native-verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--input', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/research/aspect-trials')
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    result = verify(extract_eg(args.input.read_bytes()), args.output_dir)
    print(f'Total: {result["passed_cases"]} cases including stock baseline. No capture verification.')
