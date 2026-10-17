"""Read-only XPAN leads; table bytes and pointer matches do not prove consumers."""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256


def inspect(data):
    StockImage(data)
    source, destination, length, _ = SCATTER

    def location(offset):
        rom = BASE + offset
        mapped = destination + rom - source if source <= rom < source + length else rom
        return {'rom': hex(rom), 'mapped': hex(mapped)}

    def matches(pattern):
        return [location(m.start()) for m in re.finditer(re.escape(pattern), data)]

    report = {'eg_sha256': sha256(data), 'strings': [], 'patterns': {}, 'windows': [],
              'limitations': ['Offline byte matches only; no recovered consumer, execution, camera output or patch.']}
    for m in re.finditer(rb'[ -~]{5,}', data):
        if re.search(rb'CropMode|LimitSelectableImageArea|ImageArea|ImageSize|CropArea|AspectRatio', m.group(), re.I):
            report['strings'].append(dict(location(m.start()), text=m.group().decode()))
    for name, values in [('dx_xywh', (0, 0, 5568, 3712)),
                         ('square_xywh', (928, 0, 3712, 3712)),
                         ('wide_xywh', (0, 292, 5568, 3128)),
                         ('dx_wh', (5568, 3712)), ('square_wh', (3712, 3712)),
                         ('wide_wh', (5568, 3128))]:
        for fmt in ('H', 'I'):
            report['patterns'][name + '_' + fmt] = matches(struct.pack('<' + fmt * len(values), *values))
    for start, size in [(0x10750908, 66), (0x11572990, 128),
                        (0x115A4DA8, 256), (0x12049A90, 64)]:
        raw = data[start - BASE:start - BASE + size]
        report['windows'].append(dict(location(start - BASE), bytes=raw.hex(),
                                      u16=list(struct.unpack('<' + 'H' * (size // 2), raw))))
    report['pointer_matches'] = {hex(a): matches(struct.pack('<I', a)) for a in
                                 (0x10750908, 0x115A4DA8, 0x115A4DB4, 0x115A4E20,
                                  0x115729A0, 0xC1884164)}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, default=Path('build/research/xpan-stock-scan.json'))
    args = parser.parse_args()
    result = inspect(extract_eg(args.input.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'Saved offline leads: {args.output}')
