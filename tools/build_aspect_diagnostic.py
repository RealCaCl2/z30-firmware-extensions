"""Build an explicitly opt-in full/aspect image with deferred capture tracing."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--deps', type=Path)
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.build import build
    from z30_extensions.container import extract_eg
    if a.output.exists() or a.output.with_suffix('.json').exists():
        p.error('Output or report exists')
    binary, report = build(extract_eg(a.source.read_bytes()),
                           ['full', 'aspect-profiles'], aspect_trace=True)
    report['diagnostic_only'] = True
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.with_suffix('.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    with a.output.open('xb') as f:
        f.write(binary)
    print(a.output)


if __name__ == '__main__':
    main()
