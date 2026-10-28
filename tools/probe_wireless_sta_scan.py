"""Capture stock STA candidate scan requests offline; no camera or RF access."""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import machine
from probe_wireless_config import STACK, STOP, runtime
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256


def probe(data):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    StockImage(data)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
    cases = []
    for mode, status in ((0, 0), (1, 0), (2, 0), (0, -23)):
        u = machine(data)
        # Constructor completion touches a BSS context outside the file image.
        # Zeroed fixture page, not an observed live context.
        u.mem_map(0xC145F000, 0x1000)
        # Explicit zero SSID length and BSSID pointer: candidate list-scan call.
        for reg, value in zip(regs, (0, 0, 0, mode)):
            u.reg_write(reg, value)
        captures = []

        def result(em, value=0):
            em.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
            em.reg_write(UC_ARM_REG_PC, em.reg_read(UC_ARM_REG_LR))

        def hook(em, address, size, unused):
            r0, r1, r2, r3 = [em.reg_read(reg) for reg in regs]
            if address == STOP:
                em.emu_stop()
            elif address == runtime(0x11D6CF1C):
                em.mem_write(r0, bytes(r1))
                result(em, r0)
            elif address == runtime(0x11D6CECC):
                em.mem_write(r0, bytes((r2 & 255,)) * r1)
                result(em, r0)
            elif address == runtime(0x11C9E98C):
                result(em, 123)
            elif address == runtime(0x11D6D168):
                em.reg_write(UC_ARM_REG_R1, 0)
                result(em)
            elif address == runtime(0x11D5DA3E):
                name = bytes(em.mem_read(r0, 32)).split(b'\0')[0].decode('ascii')
                payload = bytes(em.mem_read(r1, r2))
                captures.append(dict(name=name, length=r2, setting=r3,
                                     payload_hex=payload.hex(),
                                     bss_type=payload[50], scan_type=payload[51],
                                     channel_count=struct.unpack_from('<I', payload, 68)[0]))
                result(em, status)
            else:
                result(em)

        for address in (STOP,) + tuple(map(runtime, (
                0x11D5FD48, 0x11D4CCF0, 0x11D5DD10,
                0x11D6CF1C, 0x11D6CECC, 0x11C9E98C, 0x11D6D168,
                0x11D5DA3E, 0x11D4BC58, 0x11D4BC5C))):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        u.emu_start(runtime(0x11D4E100) | 1, STOP + 2, count=10000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP
        assert u.reg_read(UC_ARM_REG_SP) == STACK
        assert len(captures) == 1
        capture = captures[0]
        assert capture['name'] == 'escan' and capture['length'] == 100
        assert capture['channel_count'] == 0 and capture['bss_type'] == 2
        assert capture['scan_type'] == int(mode == 1)
        returned = u.reg_read(UC_ARM_REG_R0)
        assert returned == (0xFFFFFFFA if status else 0)
        cases.append(dict(mode=mode, synthetic_driver_status=status,
                          request=capture, returned=returned, stack_restored=True))
    return dict(stock_eg_sha256=sha256(data), cases=cases, limitations=[
        'Executes stock scan constructor and escan wrapper, not the full list-scan workflow.',
        'Interface lookup, scan-mode setup, memory helpers, tick/division and iovar transport are stubs.',
        'No explicit channel list in captured request does not override current band or country rules.',
        'No actual phone channel, scan results, menu binding, association or RF proof.',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f"Passed {len(report['cases'])} stock escan construction cases")


if __name__ == '__main__':
    main()
