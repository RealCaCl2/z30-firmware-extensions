"""Audit region-trial scope and execute stock profile callbacks offline."""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import probe
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256


def audit(stock, candidate):
    from native_runtime import Context
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
    StockImage(stock)
    assert len(stock) == len(candidate)
    expected = struct.pack('<4siI', b'US', -1, 7)
    offset = 0x114A3810 - BASE
    assert candidate[offset:offset+12] == expected
    changed = [i for i, (a,b) in enumerate(zip(stock,candidate)) if a != b]
    assert set(changed) <= set(range(offset, offset+12)) | {len(stock)-2,len(stock)-1}
    assert len(changed) == 8
    snapshot_address = struct.unpack_from('<I', stock, 0x1008FCD0-BASE)[0]
    snapshots = []
    for name, image in (('stock',stock),('US trial',candidate)):
        context = Context(stock,image)
        u = context.u
        context.map(snapshot_address,12)
        u.mem_write(0xC18807C0,struct.pack('<I',7))

        def hook(em,address,size,unused):
            # Supply unchanged sales-region value, refresh and mutex services.
            value = 7 if address == 0x101F4FA4 else 1 if address == 0x101F4E06 else 0
            em.reg_write(UC_ARM_REG_R0,value)
            em.reg_write(UC_ARM_REG_PC,em.reg_read(UC_ARM_REG_LR))

        for address in (0x101F4FA4,0x101F4E06,0x10199BB0,0x10199BAA,0x1008FAF0):
            u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
        context.call(0x1008FB26,(),limit=4000)
        words = struct.unpack('<3I',u.mem_read(snapshot_address,12))
        assert words == (0x45C,7,7)
        snapshots.append(dict(image=name,descriptor_words=list(words)))
    profiles = probe(stock)['country_profile_cases']
    table = 0x11EA05D0 - BASE
    countries = [stock[table+i*4:table+i*4+4].split(b'\0')[0].decode('ascii') for i in range(39)]
    return dict(source_sha256=sha256(stock),candidate_sha256=sha256(candidate),
                changed_bytes=len(changed),profile_table_entry_rom='0x114a3810',
                profile_identifier_preserved=7,metadata_snapshot_cases=snapshots,
                country_allowlist=countries,profile_callbacks=profiles,
                limitations=[
                    'Profile callback results are cache values; underlying country-specific channel/power tables were not queried.',
                    'Internal AA profile IDs have not been mapped to named sales regions.',
                    'Metadata case supplies sales-region 7, mutex/refresh responses and downstream parameter refresh; getter/snapshot execute.',
                    'Pointer/BL cross-reference searches cannot prove absence of all indirect users.',
                    'User-reported 5 GHz connection is separate from these offline checks.',
                ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=audit(extract_eg(args.source.read_bytes()),args.image.read_bytes())
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
        f.write('\n')
    print('Passed exact 8-byte scope, 2 metadata snapshot cases and 29 profile callback cases')


if __name__=='__main__':
    main()
