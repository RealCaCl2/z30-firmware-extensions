"""Execute stock scan band switching offline. No camera, patch or RF access."""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import machine
from probe_wireless_config import DATA, STACK, STOP, runtime
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256


def probe(data):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    StockImage(data)
    # Literal referenced by the stock LDR at 11D46C26.
    context = struct.unpack_from('<I', data, 0x11D46F44 - BASE)[0]
    cases = []
    for operation, input_band, argument, driver_status in (
            ('switch', 1, 0, 0), ('switch', 2, 0, 0),
            ('switch', 1, 1, 0), ('switch', 2, 0, -23),
            ('restore', 1, 0, 0), ('restore', 2, 0, 0),
            ('restore', 2, 1, 0), ('restore', 2, 0, -23)):
        u = machine(data)
        u.mem_write(context + 12, struct.pack('<I', DATA))
        u.mem_write(DATA, bytes((input_band,)))
        u.reg_write(UC_ARM_REG_R0, argument)
        commands, polls, delays = [], [], []

        def result(em, value=0):
            em.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
            em.reg_write(UC_ARM_REG_PC, em.reg_read(UC_ARM_REG_LR))

        def hook(em, address, size, unused):
            r0, r1, r2, r3 = [em.reg_read(r) for r in (
                UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
            if address == STOP:
                em.emu_stop()
            elif address == runtime(0x11D5D9EC):
                sp = em.reg_read(UC_ARM_REG_SP)
                setting = struct.unpack('<I', em.mem_read(sp, 4))[0]
                value = struct.unpack('<I', em.mem_read(r1, 4))[0]
                commands.append(dict(command=r0, value=value, setting=setting))
                result(em, driver_status)
            elif address == runtime(0x11D508A0):
                band = 2 if input_band == 1 else 1
                em.mem_write(r0, struct.pack('<H', 0x1006 if band == 2 else 0xD024))
                polls.append(band)
                result(em)
            elif address == runtime(0x11D52A34):
                em.mem_write(r0, b'\0\0\0\0')
                polls.append(0)
                result(em)
            elif address == runtime(0x11D46A2A):
                delays.append(r0)
                result(em)
            else:
                result(em)

        for address in (STOP,) + tuple(map(runtime, (
                0x11D5D9EC, 0x11D508A0, 0x11D52A34, 0x11D46A2A,
                0x11D4BC58, 0x11D570A2, 0x11D5C016, 0x11D5709A))):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        entry = 0x11D46C18 if operation == 'switch' else 0x11D46C9C
        u.emu_start(runtime(entry) | 1, STOP + 2, count=10000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP
        assert u.reg_read(UC_ARM_REG_SP) == STACK
        expected = [] if argument else [0 if operation == 'restore' else 2 if input_band == 1 else 1]
        assert [c['value'] for c in commands] == expected
        assert all(c['command'] == 142 and c['setting'] == 1 for c in commands)
        cases.append(dict(operation=operation, synthetic_saved_band=input_band,
                          argument=argument, synthetic_driver_status=driver_status,
                          commands=commands, polls=polls, delay_arguments=delays,
                          stack_restored=True))
    return dict(stock_eg_sha256=sha256(data), cases=cases,
                stock_locations={
                    'switch_helper_rom': '0x11d46c18',
                    'restore_helper_rom': '0x11d46c9c',
                    'switch_callers_rom': ['0x11d46d5a', '0x11d47050'],
                    'restore_callers_rom': ['0x11d46dbe', '0x11d46de0', '0x11d470e4'],
                    'candidate_scan_workflow_rom': '0x11d46e8e',
                    'scan_wrapper_rom': '0x11d4fd88',
                    'scan_request_builder_rom': '0x11d4e100',
                },
                limitations=[
                    'Driver replies, current chanspec, band state, synchronization and memory services are synthetic.',
                    'Stock switch/restore helpers and band-set ioctl wrapper execute unchanged.',
                    'Call sites occur in candidate scan workflows; UI binding and reachability are not proven.',
                    'No live allowed-channel list, association, 5 GHz connection or throughput evidence.',
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
    print(f"Passed {len(report['cases'])} stock STA candidate band-switch/restore cases")


if __name__ == '__main__':
    main()
