"""Build an explicitly opt-in soft-aspect-mask EG trial; never install it.

Two modes:
  --trial   static 2.35:1 bands enabled from boot, for the QEMU end-to-end check
  --dynamic mask follows the selected aspect profile and native viewport/source
            snapshots, independently of grid visibility
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--deps',type=Path,required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--trial',action='store_true',help='Static sample bands enabled from boot')
    mode.add_argument('--dynamic',action='store_true',help='Mask follows the selected aspect profile')
    p.add_argument('--trace',action='store_true',help='Opt-in scalar mask-context trace, exported only when opening the aspect submenu')
    a=p.parse_args()
    if a.trace and not a.dynamic:
        p.error('--trace requires --dynamic')
    if a.output.exists() or a.output.with_suffix('.json').exists():
        p.error('Output or report exists')
    sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.build import build
    from z30_extensions.container import extract_eg
    image,report=build(extract_eg(a.source.read_bytes()),'full',
                       aspect_mask=a.dynamic,aspect_mask_trial=a.trial,aspect_trace=a.trace)
    report['diagnostic_only']=True
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.with_suffix('.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2)
    with a.output.open('xb') as stream:
        stream.write(image)
    print(a.output)


if __name__=='__main__':
    main()
