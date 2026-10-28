"""Read-only Z30 C1.20 post-capture LUT research inventory.

Names, literal references and BL byte patterns are leads, not a recovered
JPEG pipeline. No camera access or patched firmware output.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256
from inspect_precapture import bl_target

REGISTRY = 0xC181099C
EFFECT_COUNT = 20
TARGETS = {
    'effect_request': 0x100CCA42,
    'effect_optional_callback': 0x100CCAB0,
    'effect_query_callback': 0x100CCAD4,
    'playback_submit': 0x10348130,
    'still_save_dispatch': 0x1033DF28,
}
ANCHORS = (
    b'PlaybackProcessS', b'ImageFormatServi', b'ImageEffectServi',
    b'StillImageFileSt', b'IMPJPGCO', b'IMPJPGEN', b'PBPMAIN',
    b'PBPSUB', b'IMGEFCT', b'YCbCrFormat = %d', b'LineBytesY  = %d',
    b'LineBytesC  = %d', b'AddressY    = 0x%08x', b'AddressC    = 0x%08x',
)
# Each is a bounded instruction window, not an inferred function length.
WINDOWS = (
    (0x100CCAB0, 0x42), (0x100CCA12, 0x5C),
    (0x10348130, 0x48), (0x10347B04, 0x20),
    (0x10347BFA, 0x10), (0x1034364E, 0x40),
    (0x1042443E, 0x20), (0x1033DF28, 0x1A),
)


def mapped(address):
    source, destination, size, _ = SCATTER
    return destination + address - source if source <= address < source + size else address


def effect_records(data):
    image = StockImage(data)
    records = []
    for index, getter in enumerate(image.words(REGISTRY, EFFECT_COUNT)):
        address = getter & ~1
        instruction = int.from_bytes(image.read(address, 2), 'little')
        if not getter & 1 or instruction & 0xF800 != 0x4800 or (instruction >> 8) & 7:
            raise ValueError('Expected Thumb getter beginning with LDR r0, [pc, #imm]')
        if image.read(address + 2, 2) != b'\x70\x47':
            raise ValueError('Expected getter to return immediately')
        literal = ((address + 4) & ~3) + (instruction & 255) * 4
        descriptor = image.words(literal, 1)[0]
        words = image.words(descriptor, 9)
        records.append(dict(index=index, getter=hex(address), literal=hex(literal),
                            descriptor=hex(descriptor),
                            descriptor_words=[hex(w) for w in words],
                            optional_callback=hex(words[6]),
                            query_callback=hex(words[7]), label=hex(words[8])))
    return records


def inspect(data):
    StockImage(data)
    anchors = []
    for name in ANCHORS:
        position = data.find(name)
        if position < 0:
            raise ValueError('Missing stock anchor: ' + repr(name))
        address = BASE + position
        anchors.append(dict(name=name.decode('ascii'), rom=hex(address),
                            runtime=hex(mapped(address))))
    calls = {name: [] for name in TARGETS}
    inverse = {address: name for name, address in TARGETS.items()}
    end = 0x105B0000 - BASE
    for offset in range(0, end, 2):
        first, second = struct.unpack_from('<HH', data, offset)
        target = bl_target(BASE + offset, first, second)
        if target in inverse:
            calls[inverse[target]].append(hex(BASE + offset))
    return dict(source_sha256=sha256(data), source_size=len(data), camera_access=False,
                registry=hex(REGISTRY), effects=effect_records(data), anchors=anchors,
                bl_scan_range=[hex(BASE), hex(BASE + end)],
                bl_candidates=calls,
                limitations=[
                    'BL patterns can be data or misaligned instructions; candidates only.',
                    'Service labels and IRQ/task names do not establish a JPEG codec ABI.',
                    'Effect table indexes have not been mapped to retouch menu operations.',
                    'No full-resolution JPEG decode, pixel edit, encode or DCF save is demonstrated.',
                    'YCbCr debug strings do not bind a buffer format to a particular retouch stage.',
                ])


def instruction_windows(data):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    lines = ['Stock bounded instruction windows. No function-length or reachability claim.']
    for address, length in WINDOWS:
        lines.append('\nWINDOW ' + hex(address))
        instructions = list(decoder.disasm(data[address - BASE:address - BASE + length], address))
        if sum(i.size for i in instructions) != length:
            raise ValueError('Incomplete instruction window at ' + hex(address))
        for i in instructions:
            lines.append(f'{i.address:08x} {i.bytes.hex():12} {i.mnemonic:10} {i.op_str}')
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/inventory.json'))
    p.add_argument('--disassembly', type=Path)
    args = p.parse_args()
    paths = [args.source.resolve(), args.output.resolve()]
    if args.disassembly:
        paths.append(args.disassembly.resolve())
    if len(set(paths)) != len(paths):
        raise ValueError('Source and output paths must be distinct')
    data = extract_eg(args.source.read_bytes())
    report = inspect(data)
    listing = instruction_windows(data) if args.disassembly else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.disassembly:
        args.disassembly.parent.mkdir(parents=True, exist_ok=True)
        args.disassembly.write_text(listing, encoding='utf-8')
    print(json.dumps(dict(effects=len(report['effects']), anchors=len(report['anchors']),
                         bl_candidate_counts={k: len(v) for k, v in report['bl_candidates'].items()}), indent=2))


if __name__ == '__main__':
    main()
