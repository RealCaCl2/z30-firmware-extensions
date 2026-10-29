"""Build the in-camera LUT trial firmware (research image, not camera-verified).

Embeds a packed 3D LUT (identity by default, or converted from a .cube file)
and the Thumb-2 LUT core, gated over the stock index-6 retouch processing
entry. Playback retouch option 0/1/2 then runs the embedded LUT on the decoded
planes and the stock encode/save path produces the copy.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.features import lut_retouch


def build(stock, cube_text=None, size=17):
    plan = Plan(stock)
    lut_retouch.add(plan, cube_text=cube_text, size=size)
    image, report = plan.finish()
    report.update(preset='lut-retouch-research', enabled_features=['lut-retouch'],
                  research=True, includes_full_features=False)
    return image, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cube', type=Path, default=None,
                        help='optional .cube file to embed (default: identity 17)')
    parser.add_argument('--size', type=int, default=17)
    args = parser.parse_args()
    metadata = args.output.with_suffix('.json')
    if args.output.exists() or metadata.exists():
        parser.error('Choose new output paths')
    cube_text = args.cube.read_text(encoding='utf-8') if args.cube else None
    image, report = build(extract_eg(args.source.read_bytes()),
                          cube_text=cube_text, size=args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(image)
    with metadata.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    feature = report['features']['lut_retouch']
    print(f"Built LUT trial image: gate {feature['gate']} -> {feature['gate_entry']}, "
          f"embedded {feature['embedded_bytes']} byte LUT; validation pending")


if __name__ == '__main__':
    main()
