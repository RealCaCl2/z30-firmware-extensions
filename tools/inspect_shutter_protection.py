"""Read-only stock string/pointer leads for shutter parking research.

No hardware access, firmware patch, or executable control API is produced.
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

PATTERN = re.compile(
    r'shutter|curtain|(?:power|shut)[_ -]?(?:off|down)|'
    r'lens.*(?:attach|detach|mount|remove|connect)|'
    r'(?:attach|detach|mount|remove|connect).*lens|'
    r'(?:mirror|mechanical|mecha).*?(?:control|service)|'
    r'ID_.*(?:LENS|POWER)', re.I)


def mapped(offset):
    source, destination, size, _ = SCATTER
    address = BASE + offset
    return destination + address - source if source <= address < source + size else address


def inspect(data):
    StockImage(data)
    hits = []
    for match in re.finditer(rb'[ -~]{5,}', data):
        value = match.group().decode('ascii')
        if not PATTERN.search(value):
            continue
        address = mapped(match.start())
        needle = struct.pack('<I', address)
        refs = []
        at = data.find(needle)
        while at >= 0:
            if at % 4 == 0:
                refs.append(hex(mapped(at)))
            at = data.find(needle, at + 1)
        hits.append(dict(address=hex(address), eg_offset=hex(match.start()),
                         text=value, aligned_pointer_candidates=refs))
    return dict(stock_eg_sha256=sha256(data), stock_eg_size=len(data),
                method='Identity/CRC/mapping-checked ASCII strings and aligned pointer byte patterns',
                limitations=[
                    'Strings may describe unused, shared, test, or non-host components.',
                    'Pointer byte patterns are not verified code cross-references.',
                    'No close/open ABI, state machine, event ordering, or shutdown power boundary recovered.',
                    'No proof of unpowered closed retention, current draw, or physical-camera behavior.',
                ], strings=hits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disassembly', type=Path, help='Optional linear windows; requires capstone')
    args = parser.parse_args()
    if args.output.resolve() == args.source.resolve():
        raise ValueError('Output must differ from source')
    payload = args.source.read_bytes()
    data = payload if len(payload) == 33939514 else extract_eg(payload)
    report = inspect(data)
    if args.disassembly:
        if args.disassembly.resolve() in (args.source.resolve(), args.output.resolve()):
            raise ValueError('Disassembly must differ from source and report')
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
        decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        lines = ['Identity-checked stock. Linear windows; data may decode as instructions.',
                 'No hardware execution. Command meanings and ABI remain unverified.']
        for address, size in ((0x101B69D8, 0x80), (0x101B6B1A, 0x2C),
                              (0x101B6C1A, 0x3E), (0x1059486C, 0x90)):
            lines.append('\nWINDOW ' + hex(address))
            for instruction in decoder.disasm(data[address-BASE:address-BASE+size], address):
                lines.append(f'{instruction.address:08x} {instruction.bytes.hex():12} '
                             f'{instruction.mnemonic:10} {instruction.op_str}')
        args.disassembly.parent.mkdir(parents=True, exist_ok=True)
        with args.disassembly.open('x', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Saved {len(report['strings'])} leads to {args.output}")


if __name__ == '__main__':
    main()
