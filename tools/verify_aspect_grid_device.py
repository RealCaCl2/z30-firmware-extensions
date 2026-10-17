"""Replay built reference-frame parameters through the stock grid driver.

Requires the existing local research tools directory. Explicit palette, device,
status and lifecycle inputs; does not prove graphics task scheduling or pixels.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--research-tools',type=Path,required=True)
    p.add_argument('--deps',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    sys.path[:0]=[str(a.research_tools.resolve()),str(a.deps.resolve())]
    from probe_grid_device_submission import GridDeviceContext,FORMAT,MAIN,sha256
    source=json.loads(a.input.read_text())
    assert source['source_sha256']==sha256(MAIN)
    context=GridDeviceContext();cases=[]
    for frame in source['reference_frame_cases']:
        viewport=frame['viewport'];fields=FORMAT.unpack(bytes.fromhex(frame['parameters']))
        item=context.submit(fields,(viewport[2],viewport[3]))
        assert item['returned']==1 and len(item['register_writes'])==7
        item.update(profile=frame['profile'],style=frame['style'],rectangle=frame['rectangle'])
        cases.append(item)
    result={'source_sha256':source['source_sha256'],'image_sha256':source['image_sha256'],
            'cases':cases,'passed_cases':len(cases),'actual_pixels_verified':False,
            'models':['Explicit palette, device handle, viewport state and zero status word.',
                      'Original bank, packer, converter and driver/validator execute.',
                      'No natural graphics worker scheduling or real display verified.'],
            'source_bindings':{name:hashlib.sha256((a.research_tools/name).read_bytes()).hexdigest()
                               for name in ('probe_grid_device_submission.py','probe_grid_controls.py')}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {len(cases)} native driver submissions, seven register writes per case; no pixel proof')
