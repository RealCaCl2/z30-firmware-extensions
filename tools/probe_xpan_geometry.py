"""Execute stock dimension selectors and buffer setup with synthetic RAM inputs.

Optional dependencies: capstone and unicorn. No camera, QEMU, JPEG encoding,
firmware patch output, or real capture context is involved.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, sha256


def probe(stock):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_MEM_READ

    context = Context(stock, stock)
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    report = {'eg_sha256': sha256(stock), 'cases': [], 'instruction_windows': {},
              'limitations': ['Synthetic RAM fixture; not menu/property or capture execution.',
                             'Only one output-table halfword is changed in emulator memory.',
                             'Internal crop/RAW tables, JPEG encoder, metadata and live view are not patched or validated.']}
    for address, size in ((0x1020BFDA, 14), (0x1020BFC8, 18),
                          (0x1020C072, 10), (0x10142BEA, 0x12C),
                          (0x1014237A, 0x88), (0x1014277A, 0xB4)):
        report['instruction_windows'][hex(address)] = [
            f'{i.address:08x} {i.bytes.hex()} {i.mnemonic} {i.op_str}'
            for i in decoder.disasm(stock[address - BASE:address - BASE + size], address)]
    expected = {0: [(5568, 3712), (4176, 2784), (2784, 1856)],
                2: [(5568, 3128), (4176, 2344), (2784, 1560)],
                4: [(3712, 3712), (2784, 2784), (1856, 1856)]}
    reads = []

    def read_hook(u, access, address, size, value, extra):
        if 0x115A4DA8 <= address < 0x115A4DE4:
            reads.append(hex(address))

    context.u.hook_add(UC_HOOK_MEM_READ, read_hook)
    obj, info, header, holder = RAM, RAM + 0x1000, RAM + 0x4000, RAM + 0x5000
    original = bytes(context.u.mem_read(0x115A4DB6, 2))
    assert original == struct.pack('<H', 3128)
    for experiment in ('stock', 'output_height_2056_only'):
        if experiment != 'stock':
            context.u.mem_write(0x115A4DB6, struct.pack('<H', 2056))
        for area in (0, 2, 4):
            for size in range(3):
                wanted = expected[area][size]
                if experiment != 'stock' and area == 2 and size == 0:
                    wanted = (5568, 2056)
                pointer = context.call(0x1020BFDA, (area, size))
                selected = struct.unpack('<2H', context.u.mem_read(pointer, 4))
                assert selected == wanted
                internal = context.call(0x1020BFC8, (0, area, 0))
                offset = context.call(0x1020C072, (area,))
                context.u.mem_write(obj, bytes(0x100))
                context.u.mem_write(info, bytes(0x3000))
                context.u.mem_write(obj + 4, struct.pack('<I', holder))
                context.u.mem_write(holder, struct.pack('<I', header))
                context.u.mem_write(info + 0x208, bytes([area]))
                context.u.mem_write(info + 0x229, bytes([size]))
                reads.clear()
                context.call(0x10142BEA, (obj, info))
                raw = bytes(context.u.mem_read(obj, 44))
                output = struct.unpack_from('<2H', raw, 0x14)
                padded = struct.unpack_from('<2H', raw, 0x10)
                assert output == wanted
                assert all(a >= b for a, b in zip(padded, output))
                assert hex(pointer) in reads and hex(pointer + 2) in reads
                buffer_reads = sorted(set(reads))
                context.u.mem_write(obj, bytes(0x100))
                context.u.mem_write(obj + 4, struct.pack('<I', holder))
                context.call(0x1014237A, (obj, info, 0))
                config = bytes(context.u.mem_read(obj, 0x70))
                report['cases'].append({
                    'experiment': experiment, 'area_index': area, 'size_index': size,
                    'selected_address': hex(pointer), 'output_wh': output,
                    'padded_wh': padded, 'stride': struct.unpack_from('<H', raw, 0xE)[0],
                    'layout_words_1c_20_24': struct.unpack_from('<3I', raw, 0x1C),
                    'native_table_reads': buffer_reads,
                    'config_22_24_candidate_wh': struct.unpack_from('<2H', config, 0x22),
                    'config_26_28_candidate_xy': struct.unpack_from('<2H', config, 0x26),
                    'config_38_3a_candidate_wh': struct.unpack_from('<2H', config, 0x38),
                    'config_hex': config.hex(),
                    'internal_wh_candidate': struct.unpack('<2H', context.u.mem_read(internal, 4)),
                    'offset_xy_candidate': struct.unpack('<2H', context.u.mem_read(offset, 4)),
                    'object_hex': raw.hex()})
    context.u.mem_write(0x115A4DB6, original)
    baseline = {(x['area_index'], x['size_index']): x for x in report['cases']
                if x['experiment'] == 'stock'}
    for case in report['cases'][9:]:
        before = baseline[case['area_index'], case['size_index']]
        if (case['area_index'], case['size_index']) == (2, 0):
            assert case['output_wh'] != case['config_22_24_candidate_wh']
            assert case['config_22_24_candidate_wh'] == before['config_22_24_candidate_wh']
            assert case['config_26_28_candidate_xy'] == before['config_26_28_candidate_xy']
            left, right = bytes.fromhex(before['config_hex']), bytes.fromhex(case['config_hex'])
            case['config_changed_byte_offsets'] = [hex(i) for i, (a, b) in enumerate(zip(left, right)) if a != b]
        else:
            assert case['config_hex'] == before['config_hex']
            assert case['object_hex'] == before['object_hex']
    report['passed_cases'] = len(report['cases'])
    report['conclusion'] = ('Native output selector and buffer setup consume 5568x2056 in the isolated experiment; '
                            'other sizes/areas retain stock dimensions. Internal geometry remains unchanged, '
                            'so this is not a complete XPAN crop patch.')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps', type=Path, help='Optional directory containing capstone and unicorn')
    parser.add_argument('--input', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/research/xpan-native-geometry.json')
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    result = probe(extract_eg(args.input.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'Passed {result["passed_cases"]} isolated native geometry cases; saved {args.output}')
