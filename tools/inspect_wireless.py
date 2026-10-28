"""Read-only wireless leads from identity-checked stock Z30 C 1.20.

Strings identify bundled components, not executed settings or hardware behavior.
No patch, firmware output, camera access, or radio configuration is performed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256


PATTERN = re.compile(
    r'wlan|wi.?fi|wireless|ptpip|ptp.ip|bcm434|broadcom|'
    r'43455c0-roml|setsockopt|ampdu|amsdu|country|qtxpower|'
    r'^nmode$|^vhtmode$|^bw_cap$|^mimo_bw_cap$|^5g_rate$', re.I)


def inspect(data):
    StockImage(data)
    source, destination, length, _ = SCATTER
    hits = []
    for match in re.finditer(rb'[ -~]{4,}', data):
        value = match.group().decode('ascii')
        if not PATTERN.search(value):
            continue
        rom = BASE + match.start()
        mapped = destination + rom - source if source <= rom < source + length else rom
        hits.append({'eg_offset': hex(match.start()), 'rom_address': hex(rom),
                     'mapped_address': hex(mapped), 'text': value})
    return {
        'stock_eg_sha256': sha256(data), 'stock_eg_size': len(data),
        'method': 'Printable ASCII strings from hash/CRC/mapping-validated stock EG',
        'limitations': [
            'No call graph, driver ABI, active settings, or transfer bottleneck recovered.',
            'Bundled chip/board identifiers do not prove the fitted hardware or active firmware.',
            'Socket diagnostics may belong to tests; ping diagnostics occur nearby.',
            'Radio firmware strings are not Nikon host CPU callable APIs.',
            'No physical-camera, throughput, 5 GHz, or SD-module validation.',
        ],
        'strings': hits,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Saved {len(report['strings'])} string leads to {args.output}")


if __name__ == '__main__':
    main()
