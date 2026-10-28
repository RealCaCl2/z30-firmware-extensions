"""Read-only pre-capture research leads from the identity-checked stock image.

No camera access, code execution, patch creation or firmware output. Halfword
BL patterns and aligned pointers are candidates, not a recovered call graph.
"""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256


TARGETS = {
    'pool_register': 0x100CC3A2,
    'pool_acquire_candidate': 0x100CC512,
    'pool_release_candidate': 0x100CC588,
    'pool_free_count': 0x100CC63C,
    'pool_total_count': 0x100CC65A,
}
ANCHORS = (
    'CaptureDataControl.c', 'CaptureStateControlServiceStd.c',
    'ReleaseStateControl.c', 'ContinuousReleaseControl.c',
    'CaptureService.c', 'CaptureSequenceService.c',
    'CaptureInformationService.c', 'ImageBufferServi', 'CImageBufferBase',
    'ID_SW0_RELEASE_HALF', 'ID_SW0_RELEASE_ON',
)
WINDOWS = (
    (0x10141F60, 0x68),  # Fallback allocation and release on occupied slot.
    (0x1014393A, 0x56),  # Availability checks across several pools.
    (0x10143990, 0xA0),  # Stored handle and pool ID, then paired release.
    (0x10146C54, 0x26),  # Conditional 124 MiB request, not live capacity.
    (0x101E76BA, 0x88),  # Separate allocator consumer; format unbound.
)


def disassembly(data, report):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    count = 0
    for name, items in report['bl_candidates'].items():
        for item in items:
            site = int(item['site'], 16)
            instructions = list(decoder.disasm(bytes.fromhex(item['bytes']), site))
            if (len(instructions) != 1 or instructions[0].mnemonic != 'bl' or
                    int(instructions[0].op_str.lstrip('#'), 16) != TARGETS[name]):
                raise ValueError('Candidate does not decode as the expected BL')
            count += 1
    report['capstone_checked_bl_encodings'] = count
    report['limitations'].append('Standalone BL decoding does not establish reachable instruction boundaries.')
    lines = ['Offline stock instruction windows; no execution or frame-format binding.']
    for start, size in WINDOWS:
        lines.append('\nWINDOW ' + hex(start))
        for instruction in decoder.disasm(data[start - BASE:start - BASE + size], start):
            lines.append(f'{instruction.address:08x} {instruction.bytes.hex():12} '
                         f'{instruction.mnemonic:10} {instruction.op_str}')
    return '\n'.join(lines) + '\n'


def bl_target(address, first, second):
    """Decode only Thumb-2 immediate BL; ignore BLX and other branches."""
    if first & 0xF800 != 0xF000 or second & 0xD000 != 0xD000:
        return None
    sign = (first >> 10) & 1
    i1 = 1 ^ ((second >> 13) & 1) ^ sign
    i2 = 1 ^ ((second >> 11) & 1) ^ sign
    offset = (sign << 24 | i1 << 23 | i2 << 22 |
              (first & 0x3FF) << 12 | (second & 0x7FF) << 1)
    if sign:
        offset -= 1 << 25
    return (address + 4 + offset) & 0xFFFFFFFF


def mapped_address(offset):
    rom = BASE + offset
    source, destination, size, _ = SCATTER
    return destination + rom - source if source <= rom < source + size else rom


def inspect(data):
    StockImage(data)
    strings = [(m.start(), m.group().decode('ascii'))
               for m in re.finditer(rb'[ -~]{6,}', data)]
    explicit = [{'address': hex(mapped_address(at)), 'text': value[:300]}
                for at, value in strings
                if re.search(r'pre[ _-]?(?:capture|release|shoot)', value, re.I)]
    anchors = []
    for at, value in strings:
        if value not in ANCHORS:
            continue
        address = mapped_address(at)
        refs = []
        needle = struct.pack('<I', address)
        pos = data.find(needle)
        while pos >= 0:
            if pos % 4 == 0:
                refs.append(hex(mapped_address(pos)))
            pos = data.find(needle, pos + 1)
        anchors.append(dict(address=hex(address), text=value,
                            aligned_pointer_candidates=refs))
    consumers = {name: [] for name in TARGETS}
    by_target = {value: name for name, value in TARGETS.items()}
    # Bounded range includes known release, capture, pool and menu code.
    # It also includes literal pools/data: never classify these as proven calls.
    end = min(len(data) - 4, 0x10700000 - BASE)
    for at in range(0, end + 1, 2):
        first, second = struct.unpack_from('<HH', data, at)
        target = bl_target(BASE + at, first, second)
        if target in by_target:
            consumers[by_target[target]].append({
                'site': hex(BASE + at),
                'bytes': data[at:at + 4].hex(),
                'preceding_16_bytes': data[max(0, at - 16):at].hex(),
            })
    return {
        'source_sha256': sha256(data), 'source_size': len(data),
        'scope': 'Stock ASCII strings, aligned pointers and Thumb BL byte patterns only',
        'bl_scan_range': [hex(BASE), hex(BASE + end + 4)],
        'explicit_precapture_string_hits': explicit,
        'anchors': anchors,
        'targets': {name: hex(value) for name, value in TARGETS.items()},
        'bl_candidate_counts': {name: len(items) for name, items in consumers.items()},
        'bl_candidates': consumers,
        'limitations': [
            'Byte patterns may be data or a second halfword of another instruction.',
            'Indirect calls, tail branches and instructions outside the range are omitted.',
            'Names and nearby bytes do not bind RAW/JPEG stages or buffer ownership.',
            'No live camera memory measurement or capture validation is performed.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disassembly', type=Path, help='Optional instruction windows; requires capstone')
    args = parser.parse_args()
    payload = args.source.read_bytes()
    data = payload if len(payload) == 33939514 else extract_eg(payload)
    report = inspect(data)
    # Prevent replacing the input, including path aliases.
    if args.output.resolve() == args.source.resolve():
        raise ValueError('Research output must differ from the source firmware')
    if args.disassembly:
        if args.disassembly.resolve() in (args.source.resolve(), args.output.resolve()):
            raise ValueError('Disassembly must differ from the source and JSON output')
        windows = disassembly(data, report)
        args.disassembly.parent.mkdir(parents=True, exist_ok=True)
        args.disassembly.write_text(windows, encoding='utf-8')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in (
        'source_sha256', 'bl_candidate_counts')}, indent=2))
    print('Explicit pre-capture names:', len(report['explicit_precapture_string_hits']))


if __name__ == '__main__':
    main()
