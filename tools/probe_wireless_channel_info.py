"""Execute stock channel conversion and per-channel information processing.

Driver replies are synthetic. Returned metadata is not proof that a channel is
permitted, supported, usable for an AP, or actually transmitting.
"""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import machine
from probe_wireless_config import DATA, STACK, STOP, runtime
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256


def probe(data):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
    StockImage(data)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    conversions = []
    for version in (1, 2):
        for channel in (1, 11, 36, 149, 157):
            u = machine(data)
            u.mem_write(0xC12020D4, struct.pack('<I', version))
            u.mem_write(DATA, struct.pack('<H', channel))
            u.reg_write(UC_ARM_REG_R0, DATA)
            u.hook_add(UC_HOOK_CODE, lambda em, a, n, _: em.emu_stop(), begin=STOP, end=STOP)
            u.emu_start(runtime(0x11D5FD0A) | 1, STOP + 2, count=100)
            assert u.reg_read(UC_ARM_REG_PC) == STOP and u.reg_read(UC_ARM_REG_SP) == STACK
            converted = struct.unpack('<H', u.mem_read(DATA, 2))[0]
            expected = ((0x2B00 if channel <= 14 else 0x1B00) if version == 1
                        else (0x1000 if channel <= 14 else 0xD000)) | channel
            assert converted == expected and u.reg_read(UC_ARM_REG_R0) == 0
            conversions.append(dict(interface_version=version, channel=channel, chanspec=hex(converted)))

    information = []
    for version, statuses, flags in ((2, (0, 0), (7, 5)),
                                      (1, (0, 0), (7, 0xAB000030)),
                                      (2, (0, -23), (7, 0))):
        u = machine(data)
        u.mem_write(0xC12020D4, struct.pack('<I', version))
        u.mem_write(DATA + 0x100, struct.pack('<2H', 36, 149))
        for reg, value in zip(regs, (2, DATA + 0x100, DATA + 0x200, DATA + 0x300)):
            u.reg_write(reg, value)
        calls = []

        def hook(emulator, address, size, unused):
            r0, r1, r2, r3 = [emulator.reg_read(r) for r in regs]
            if address == STOP:
                emulator.emu_stop()
            elif address == runtime(0x11D5FD48):
                emulator.mem_write(r0, b'\0\0')
                result(emulator)
            elif address == runtime(0x11D5FB72):
                index = len(calls)
                assert index < 2
                calls.append(dict(chanspec=hex(r0), interface=r2, bss=r3,
                                  synthetic_status=statuses[index], synthetic_flags=hex(flags[index])))
                if statuses[index] == 0:
                    emulator.mem_write(r1, struct.pack('<I', flags[index]))
                result(emulator, statuses[index])
            else:
                result(emulator)  # Stock error-log function.

        for address in (STOP, runtime(0x11D5FD48), runtime(0x11D5FB72), runtime(0x11D4BC5C)):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        u.emu_start(runtime(0x11D53718) | 1, STOP + 2, count=3000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP and u.reg_read(UC_ARM_REG_SP) == STACK
        returned = u.reg_read(UC_ARM_REG_R0)
        count = struct.unpack('<I', u.mem_read(DATA + 0x300, 4))[0]
        expected_count = 1 if statuses[1] else 2
        assert count == expected_count and len(calls) == 2
        assert returned == (0xFFFFFFF6 if statuses[1] else 0), (version, statuses, hex(returned))
        processed = []
        for index in range(count):
            record = bytes(u.mem_read(DATA + 0x200 + index * 8, 8))
            value = struct.unpack_from('<I', record)[0]
            auxiliary = record[4]
            assert value == flags[index] & 0x7F
            assert auxiliary == ((flags[index] >> 24) & 255 if flags[index] & 0x10 else 0)
            processed.append(dict(flags=hex(value), auxiliary=auxiliary))
        information.append(dict(interface_version=version, calls=calls,
                                returned=returned, processed_count=count, records=processed))
    return dict(stock_eg_sha256=sha256(data), conversions=conversions,
                channel_information_cases=information,
                limitations=[
                    'Driver interface version, interface context and all channel replies are synthetic.',
                    'Conversion executes stock instructions without driver stubs.',
                    'Information processing stubs interface lookup, per_chan_info and logging.',
                    'A successful information query does not itself accept or select a channel.',
                    'No effective regulatory domain, hardware, connection, RF or throughput verification.',
                ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = probe(extract_eg(options.source.read_bytes()))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f"Passed {len(report['conversions'])} stock channel-conversion and "
          f"{len(report['channel_information_cases'])} channel-information offline cases")


if __name__ == '__main__':
    main()
