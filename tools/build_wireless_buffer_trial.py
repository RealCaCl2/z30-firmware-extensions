"""Build a US-region plus 128 KiB socket-send-buffer trial; no device access."""
import argparse
import binascii
import json
import struct
from pathlib import Path

from build_wireless_region_trial import BEFORE, ENTRY, verify as verify_region
from probe_wireless_send import probe, probe_options
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256

BUFFER_SITE = 0x11CB7FBE


def verify_protocol_option(image):
    """Execute stock option validation/dispatch to a synthetic protocol hook.

    No actual TCP allocator or high-water mark implementation is modeled.
    """
    from probe_wireless_band import machine
    from probe_wireless_config import DATA, STACK, STOP, runtime
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC)
    cases=[]
    for size,length,status in ((65536,4,0),(131072,4,0),(262144,4,0),
                               (131072,4,-1),(131072,2,0)):
        u=machine(image)
        u.mem_map(0xC140E000,0x1000)
        # Socket 1 has a synthetic valid descriptor and protocol method table.
        u.mem_write(0xC140E750+12,struct.pack('<3I',1,DATA+0x100,DATA+0x200))
        u.mem_write(DATA+0x100+0x1C,struct.pack('<I',DATA+0x301))
        u.mem_write(DATA,struct.pack('<I',size))
        u.mem_write(STACK,struct.pack('<I',length))
        regs=(UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)
        for reg,value in zip(regs,(1,0xFFFF,0x1001,DATA)):
            u.reg_write(reg,value)
        calls=[]

        def hook(em,address,n,unused):
            if address==STOP:
                em.emu_stop()
                return
            args=[em.reg_read(r) for r in regs]
            if address==DATA+0x300:
                calls.append(dict(option=args[1],setting=args[2],
                                  value=struct.unpack('<I',em.mem_read(args[3],4))[0]))
                result=status
            else:
                result=0  # errno service, used by invalid length case.
            em.reg_write(UC_ARM_REG_R0,result & 0xFFFFFFFF)
            em.reg_write(UC_ARM_REG_PC,em.reg_read(UC_ARM_REG_LR))

        for address in (STOP,DATA+0x300,runtime(0x11D231B0)):
            u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
        u.emu_start(runtime(0x11D32400)|1,STOP+2,count=2000)
        assert u.reg_read(UC_ARM_REG_PC)==STOP and u.reg_read(UC_ARM_REG_SP)==STACK
        assert calls==([dict(option=12,setting=1,value=size)] if length==4 else [])
        returned=u.reg_read(UC_ARM_REG_R0)
        assert returned == ((status & 0xFFFFFFFF) if length==4 else 0xFFFFFFFF)
        cases.append(dict(size=size,length=length,synthetic_protocol_status=status,
                          protocol_calls=calls,returned=returned,stack_restored=True))
    return cases


def build(stock):
    StockImage(stock)
    plan=Plan(stock)
    plan.edit(ENTRY,struct.pack('<4siI',b'US',-1,7),
              'Profile 7: US/-1 (same as user-tested region trial)',BEFORE)
    # R1 is 16 here. Shift 12 -> 13 requests 128 KiB, retaining the original
    # accepted-address length and every later socket option.
    plan.edit(BUFFER_SITE,b'\x49\x03','Socket SO_SNDBUF request: 64 -> 128 KiB',b'\x09\x03')
    candidate,report=plan.finish()
    assert len(candidate)==len(stock) and binascii.crc_hqx(candidate,0)==0
    allowed={len(stock)-2,len(stock)-1}
    for edit in plan.edits:
        allowed.update(range(edit.offset,edit.offset+len(edit.expected)))
    changes={i for i,(a,b) in enumerate(zip(stock,candidate)) if a!=b}
    assert changes<=allowed
    # No modification to request segmentation, TCP_NODELAY, send loop or sleep.
    assert candidate[0x11CB7D98-BASE:0x11CB7E58-BASE]==stock[0x11CB7D98-BASE:0x11CB7E58-BASE]
    region=verify_region(stock,candidate,'US',-1)
    captures=[dict(image='stock',result=probe_options(stock)),
              dict(image='128KiB',result=probe_options(candidate,131072)),
              dict(image='128KiB-rejected',result=probe_options(candidate,131072,-1))]
    report.update(trial='wifi-us-sndbuf128',candidate_sha256=sha256(candidate),
                  changed_bytes=len(changes),requested_send_buffer=131072,
                  status='offline_candidate_throughput_unverified',
                  verification=dict(region=region,socket_setup=captures,
                                    protocol_dispatch=verify_protocol_option(candidate),
                                    unchanged_stock_send_loop=probe(stock)),
                  limitations=[
                      'Requested socket buffer only, not measured effective size or allocated RAM.',
                      'Actual protocol handler/allocation ceiling is not verified; it may reject or clamp.',
                      'Stock accepted-socket caller ignores option failure; injected rejection still continues.',
                      'No live binding of user transfer to this exact accepted socket instance.',
                      'PTP/IP segmentation and polling unchanged; no measured speedup.',
                      'US region base has user-reported 5 GHz connection; this new buffer image is untested on camera.',
                      'No new QEMU page, real SD update, cold boot, file-integrity or rollback verification.',
                  ])
    return candidate,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    metadata=args.output.with_suffix('.json')
    if args.output.exists() or metadata.exists():
        parser.error('Output and report must be new files')
    candidate,report=build(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as f:f.write(candidate)
    with metadata.open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
        f.write('\n')
    print('Built US + 128 KiB candidate; socket setup/failure, option dispatch, region and unchanged send-loop checks passed')


if __name__=='__main__':
    main()
