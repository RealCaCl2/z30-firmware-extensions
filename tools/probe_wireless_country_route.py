"""Execute host country request and embedded SDK-to-country dispatch offline.

Two emulator instances model the explicit IPC payload handoff. An explicit
queue handoff invokes the original internal dispatcher after request creation.
No physical IPC, scheduler, wireless driver or camera is modeled.
"""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import machine
from probe_wireless_config import DATA, STACK, STOP, runtime, SOURCE, DESTINATION
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256


def probe(data):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    StockImage(data)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    table_offset = SOURCE - BASE + 0x1FFDB4
    assert struct.unpack_from('<I', data, table_offset + 0x64 * 4)[0] == runtime(0x11CB7A72) | 1
    assert struct.unpack_from('<2I', data, 0x11D74D0C - BASE) == (11, runtime(0x11D106DC) | 1)
    cases = []
    for country, revision in ((b'AA', 1), (b'AA', 3), (b'AA', 0), (b'CN', 0), (b'US', -1)):
        context = Context(data, data)
        u = context.u
        u.mem_write(RAM + 0x100, country + b'\0')
        requests = []

        def host_hook(emulator, address, size, unused):
            r0, r1, r2, r3 = [emulator.reg_read(r) for r in regs]
            if address == 0x10476C7C:
                payload = struct.unpack('<4I', emulator.mem_read(r2, 16))
                requests.append(dict(group=r0, command=r1, mode=r3, payload=list(payload)))
                result(emulator, 2)  # Synthetic queued response.
            elif address == 0x102354E8:
                result(emulator, len(bytes(emulator.mem_read(r0, 16)).split(b'\0')[0]))
            else:
                result(emulator)

        for address in (0x10476C7C, 0x102354E8, 0x10204B0C, 0x10482F8A, 0x10482F9E):
            u.hook_add(UC_HOOK_CODE, host_hook, begin=address, end=address)
        host_return = context.call(0x10482DA6, (RAM + 0x100, revision & 0xFFFFFFFF, 0x103C7715), limit=3000)
        assert host_return == 2
        assert requests == [dict(group=2, command=0x64, mode=1,
                                 payload=[RAM + 0x100, revision & 0xFFFFFFFF, 0, 0])]

        # Both fixtures use the same synthetic DATA address. Transfer bytes
        # explicitly, rather than claiming shared state or a real IPC exchange.
        u = machine(data)
        u.mem_write(DATA, struct.pack('<4I', *requests[0]['payload']))
        u.mem_write(DATA + 0x100, country + b'\0')
        u.mem_write(0xC1200878, struct.pack('<I', 1))
        u.mem_write(0xC12007F0, struct.pack('<I', 0))
        queued, notifications = [], []

        def string(address):
            return bytes(u.mem_read(address, 32)).split(b'\0')[0]

        def embedded_hook(emulator, address, size, unused):
            r0, r1, r2, r3 = [emulator.reg_read(r) for r in regs]
            if address == STOP:
                emulator.emu_stop()
            elif address == runtime(0x11D6CB04):
                result(emulator, len(string(r0)))
            elif address == runtime(0x11D0E084):
                result(emulator, DATA + 0x2000)
            elif address == runtime(0x11D12078):
                emulator.mem_write(r0, bytes(emulator.mem_read(r1, r2)))
                result(emulator, r0)
            elif address == runtime(0x11D0E090):
                queued.append(bytes(emulator.mem_read(r0, 0x1C)))
                result(emulator)
            elif address == runtime(0x11D0DF6C):
                result(emulator)
            elif address == runtime(0x11D6CFDC):
                left, right = string(r0), string(r1)
                result(emulator, ((left > right) - (left < right)) & 0xFFFFFFFF)
            elif address == runtime(0x11D6CA88):
                emulator.mem_write(r0, string(r1) + b'\0')
                result(emulator, r0)
            elif address == runtime(0x11D1791E):
                pointer, length = struct.unpack('<2I', emulator.mem_read(r3, 8))
                notifications.append(dict(group=r0, command=r1, length=length,
                                          status=struct.unpack('<H', emulator.mem_read(pointer, 2))[0]))
                result(emulator)

        hooks = [STOP] + [runtime(a) for a in (0x11D6CB04, 0x11D0E084, 0x11D12078,
                 0x11D0E090, 0x11D0DF6C, 0x11D6CFDC, 0x11D6CA88, 0x11D1791E)]
        for address in hooks:
            u.hook_add(UC_HOOK_CODE, embedded_hook, begin=address, end=address)

        def call(entry, argument):
            u.reg_write(UC_ARM_REG_R0, argument)
            u.reg_write(UC_ARM_REG_SP, STACK)
            u.reg_write(UC_ARM_REG_LR, STOP | 1)
            u.emu_start(runtime(entry) | 1, STOP + 2, count=5000)
            assert u.reg_read(UC_ARM_REG_PC) == STOP
            assert u.reg_read(UC_ARM_REG_SP) == STACK
            return u.reg_read(UC_ARM_REG_R0)

        sdk_return = call(0x11CB7A72, DATA)
        assert sdk_return == 2 and len(queued) == 1
        assert queued[0][0xC:0xE] == b'\x19\x0b'
        assert queued[0][0x14:0x18].split(b'\0')[0] == country
        assert struct.unpack_from('<i', queued[0], 0x18)[0] == revision
        dispatch_return = call(0x11D0E714, DATA + 0x2000)
        assert dispatch_return == 0 and len(notifications) == 1
        assert notifications[0] == dict(group=0x1A, command=11, length=2,
                                        status=int(country == b'AA' and revision == 0))
        stored_country = string(0xC1200808).decode('ascii')
        stored_revision = struct.unpack('<i', u.mem_read(0xC120080C, 4))[0]
        band, mask = struct.unpack('<2I', u.mem_read(0xC1201F2C, 8))
        cases.append(dict(input_country=country.decode('ascii'), input_revision=revision,
                          host_ipc=requests[0], host_return=host_return, sdk_return=sdk_return,
                          internal_request_hex=queued[0].hex(), dispatch_return=dispatch_return,
                          notifications=notifications, stored_country=stored_country,
                          stored_revision=stored_revision, bandwidth_band=band, bandwidth_mask=mask))
    return dict(stock_eg_sha256=sha256(data), cases=cases,
                limitations=[
                    'Country/revision are synthetic inputs, not a camera region dump.',
                    'Host IPC return, callback registration and cache maintenance are stubbed.',
                    'The IPC payload is explicitly handed to a separate emulator fixture.',
                    'Internal allocation, queue, wakeup, string helpers and replies are stubbed.',
                    'Queue handoff is explicit; the stock internal dispatcher and country callback execute.',
                    'No complete boot, hardware, country enforcement, RF or throughput validation.',
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
    print('Passed 5 host-IPC / SDK / internal-country-dispatch offline cases')


if __name__ == '__main__':
    main()
