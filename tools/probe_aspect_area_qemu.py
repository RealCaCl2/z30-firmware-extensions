"""Read native user-area and capture-area properties in an assisted stock guest.

No substituted property APIs, no camera I/O and no claim of capture completion.
"""
import argparse
import json
import sys
from pathlib import Path


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    from display_session import Session
    s=Session(str(a.output_dir.resolve()));rows=[]
    def get(key):return int(s.input(0x10465984,[key])['registers']['r0'],16)
    try:
        s.input(0x102355ee,[0xc182e688,1]);s.run_for(1)
        for key,value in ((0x501,0),(0x189e,2),(0x501,2),(0x501,4),(0x501,0)):
            before={hex(k):get(k) for k in (0x501,0x189e)}
            result=s.input(0x10465990,[key,value]);s.run_for(.5)
            after={hex(k):get(k) for k in (0x501,0x189e)}
            row={'write_key':hex(key),'write_value':value,'return':result['registers']['r0'],
                 'before':before,'after':after}
            rows.append(row);print(json.dumps(row),flush=True)
        s.audit.append({'native_area_probe':rows,'scope':__doc__});s.write_audit()
    finally:s.close()
