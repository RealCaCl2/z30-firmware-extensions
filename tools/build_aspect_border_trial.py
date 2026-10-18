"""Build the explicitly opt-in independent-border EG trial; never install it."""
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
    p.add_argument('--diagnostic',action='store_true',help='Append border counters to WL-ASPECT.DBG menu samples')
    p.add_argument('--lease-channel',action='store_true',help='Temporarily enable only an empty disabled native rectangle channel')
    a=p.parse_args()
    if a.output.exists() or a.output.with_suffix('.json').exists():
        p.error('Output or report exists')
    sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.build import build
    from z30_extensions.container import extract_eg
    image,report=build(extract_eg(a.source.read_bytes()),'full',aspect_trace=True,aspect_border=True,
                       aspect_border_diagnostic=a.diagnostic,aspect_border_lease=a.lease_channel)
    report['diagnostic_only']=True
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.with_suffix('.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2)
    with a.output.open('xb') as stream:
        stream.write(image)
    print(a.output)


if __name__=='__main__':
    main()
