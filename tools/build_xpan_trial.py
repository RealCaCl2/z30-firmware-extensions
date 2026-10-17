"""Build a QEMU/Unicorn research EG for coordinated XPAN geometry experiments.

Replaces existing area index 2 geometry. Not a camera update or a completed
feature: menu, live-view, RAW and alternate processing paths are unvalidated.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan

# Addresses and expected words are bound to exact stock C1.20 by Plan.
# Keep width and original edge/padding differences. The seven-entry variant
# table is not a menu enum; variant meanings remain research questions.
CHANGES = (
    (0x115A4D56, 292, 828, 'area 2 vertical internal offset'),
    (0x115A4DB6, 3128, 2056, 'area 2 JPEG large height'),
    (0x115A4DBA, 2344, 1544, 'area 2 JPEG medium height'),
    (0x115A4DBE, 1560, 1024, 'area 2 JPEG small height'),
    (0x115A4DF2, 3144, 2072, 'area 2 internal large height, including 16 edge rows'),
    (0x115A4DFA, 2358, 1558, 'area 2 internal reduced height, including 14 edge rows'),
    (0x115A4E3A, 3128, 2056, 'area 2 crop variant 0 height'),
    (0x115A4E3E, 3128, 2056, 'area 2 crop variant 1 height'),
    (0x115A4E42, 3120, 2048, 'area 2 crop variant 2 height'),
    (0x115A4E46, 2344, 1544, 'area 2 crop variant 3 height'),
    (0x115A4E4A, 2344, 1544, 'area 2 crop variant 4 height'),
    (0x115A4E4E, 2346, 1546, 'area 2 crop variant 5 height'),
)


def build_trial(stock):
    plan = Plan(stock)
    for address, before, after, name in CHANGES:
        plan.edit(address, struct.pack('<H', after), 'XPAN research: ' + name,
                  struct.pack('<H', before))
    candidate, report = plan.finish()
    report.update(
        trial='xpan-area2-geometry-research', status='emulator_only_not_camera_ready',
        replaces='Existing area index 2; no new menu entry or label',
        target_jpeg_wh=[[5568, 2056], [4176, 1544], [2784, 1024]],
        target_large_in_dx_xywh=[0, 828, 5568, 2056],
        limitations=[
            'Research EG only; not a complete Nikon update container. Do not install on a camera.',
            'Global table edits affect every consumer; a live JPEG-only gate has not been implemented.',
            'RAW/alternate branches retain separate stock tables and are not coherent with this trial.',
            'No real sensor, ISP, JPEG encoder, SD file or display crop validation.',
            'Medium/small heights are integer approximations; exact ratio and alignment policy are experimental.',
        ])
    return candidate, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/research/xpan-trial')
    args = parser.parse_args()
    candidate, report = build_trial(extract_eg(args.input.read_bytes()))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'xpan-research.eg.bin').write_bytes(candidate)
    (args.output_dir / 'xpan-research.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Built emulator-only XPAN research EG in {args.output_dir}')
