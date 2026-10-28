"""Execute stock host mode dispatch and complete embedded AP callback offline.

The two fixtures are independent: no UI, real queue/IPC, driver or camera is
modeled. Radio/IP services return injected values. The original AP callback
runs through its notification and return, rather than stopping at channel set.
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
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    stock = StockImage(data)
    registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    # This is the actual stock scatter table, not a hand-built callback table.
    assert stock.words(0x12079DC0, 5) == (0, 0x103A667D, 0x103A68ED, 0x103A6DF7, 0)
    assert stock.words(0x12079DD4, 5) == (0, 0x103A669D, 0x103A68ED, 0x103A6DF7, 0)
    assert stock.words(0x11D74CCC, 2) == (3, runtime(0x11D0FEC6) | 1)
    host_cases = []
    for family, mode, busy, dispatch_success in (
            (0, 0, False, True), (0, 1, False, True),
            (0, 2, False, True), (0, 1, True, True),
            (0, 1, False, False), (1, 0, False, True), (1, 1, False, True)):
        context = Context(data, data)
        u = context.u
        context.map(0xC291F000, 0x1000)
        u.mem_write(0xC291FA78, bytes(0xA0))
        u.mem_write(0xC18807C0, struct.pack('<I', 7))  # Synthetic stored index.
        if busy:
            u.mem_write(0xC18F812C, b'\x01')
        requests, refreshes = [], []

        def host_hook(emulator, address, size, unused):
            r0, r1, r2, r3 = [emulator.reg_read(r) for r in registers]
            if address == 0x101F4E06:
                refreshes.append(r0)
                result(emulator, 1)
            elif address == 0x10392F00:
                words = struct.unpack('<6I', emulator.mem_read(r0, 24))
                country = (bytes(emulator.mem_read(words[3], 3)).split(b'\0')[0].decode('ascii')
                           if words[3] else None)
                requests.append(dict(enables=list(words[:2]), mode_byte=words[2] & 255,
                                     country=country, revision=words[4]))
                result(emulator, int(dispatch_success))
            else:
                result(emulator)  # Callback registration only.

        for address in (0x10392E8E, 0x10392ED0, 0x101F4E06, 0x10392F00):
            u.hook_add(UC_HOOK_CODE, host_hook, begin=address, end=address)
        returned = context.call(0x103A64BE if family == 0 else 0x103A64CC,
                                (mode, RAM + 0x100), limit=5000)
        saved_mode = bytes(u.mem_read(0xC291FA79, 1))[0]
        state = bytes(u.mem_read(0xC291FA78, 1))[0]
        task_state = bytes(u.mem_read(0xC18F812C + family * 20, 1))[0]
        supported = not busy and (family == 1 or mode in (0, 1))
        assert len(requests) == int(supported)
        assert returned == int(supported and dispatch_success)
        if supported:
            expected_mode = 0 if family else mode
            assert saved_mode == expected_mode
            assert requests[0] == dict(enables=[0, 1], mode_byte=1 - expected_mode,
                                      country=None if expected_mode else 'AA',
                                      revision=0xFFFFFFFF if expected_mode else 7)
            assert refreshes == ([] if expected_mode else [0x47])
            assert state == int(dispatch_success)
            assert task_state == (2 if dispatch_success else 0)
        host_cases.append(dict(family=family, input_mode=mode, task_busy=busy,
                               injected_dispatch_success=dispatch_success, returned=returned,
                               saved_mode=saved_mode, wireless_state=state,
                               task_state=task_state, requests=requests,
                               settings_refreshes=refreshes))

    # Complete AP callback. IP/DHCP setup is skipped by explicit input flags;
    # all external calls reached on that path are individually intercepted.
    services = {
        0x11D6CB04: 'strlen', 0x11D513EE: 'ap_mode', 0x11D50F62: 'ssid',
        0x11D51256: 'set_channel', 0x11D52BDE: 'auto_control',
        0x11D5125E: 'get_channel', 0x11D1153C: 'coexistence_choice',
        0x11D511DE: 'security_key', 0x11D5105E: 'security_open',
        0x11D2FAB2: 'ip_presence', 0x11D2F8FA: 'ip_remove',
        0x11D0FD5A: 'ip_config', 0x11D518AA: 'radio_option_clear',
        0x11D5188C: 'radio_option_set', 0x11D514B6: 'radio_finalize',
        0x11D1791E: 'notification',
    }
    definitions = [(ch, None) for ch in (0, 1, 6, 11, 12, 36, 149)]
    definitions += [(6, name) for name in (
        'ap_mode', 'ssid', 'set_channel', 'security_open', 'ip_config',
        'radio_option_set', 'radio_finalize')]
    definitions += [(0, 'auto_control')]
    ap_cases = []
    for channel, fault in definitions:
        u = machine(data)
        config = DATA + 0x100
        u.mem_write(DATA, struct.pack('<2I', 0, config))
        u.mem_write(config, bytes((channel,)))
        u.mem_write(config + 8, b'fixture\0')
        u.mem_write(0xC12007F0, struct.pack('<I', 1))
        u.reg_write(UC_ARM_REG_R1, DATA)
        calls, notifications = [], []

        def ap_hook(emulator, address, size, unused):
            if address == STOP:
                emulator.emu_stop()
                return
            rom = address - runtime(0)
            name = services[rom]
            args = [emulator.reg_read(r) for r in registers]
            r0, r1, r2, r3 = args
            calls.append(dict(service=name, arguments=args))
            if name == 'strlen':
                value = len(bytes(emulator.mem_read(r0, 128)).split(b'\0')[0])
            elif name == 'notification':
                pointer, length = struct.unpack('<2I', emulator.mem_read(r3, 8))
                notifications.append(dict(group=r0, command=r1, count=r2, length=length,
                                          status=struct.unpack('<H', emulator.mem_read(pointer, 2))[0]))
                value = 0
            elif name == 'get_channel':
                emulator.mem_write(r0, struct.pack('<H', 0x1006))
                value = 0
            else:
                value = -23 if name == fault else 0
            result(emulator, value)

        for rom in services:
            address = runtime(rom)
            u.hook_add(UC_HOOK_CODE, ap_hook, begin=address, end=address)
        u.hook_add(UC_HOOK_CODE, ap_hook, begin=STOP, end=STOP)
        u.emu_start(runtime(0x11D0FEC6) | 1, STOP + 2, count=10000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP
        assert u.reg_read(UC_ARM_REG_SP) == STACK
        returned = u.reg_read(UC_ARM_REG_R0)
        state = struct.unpack('<I', u.mem_read(0xC12007F0, 4))[0]
        assert returned == 0  # Success/failure is reported asynchronously.
        assert notifications == [dict(group=0x1A, command=3, count=1, length=2,
                                      status=int(fault is not None))]
        assert state == (1 if fault else 2)
        selected = [c['arguments'][0] for c in calls if c['service'] == 'set_channel']
        expected = [0] if channel == 0 else [0x1000 | channel] if channel <= 11 else []
        if fault in ('ap_mode', 'ssid', 'auto_control'):
            expected = []
        assert selected == expected
        names = [c['service'] for c in calls]
        assert names[-1] == 'notification'
        if fault:
            assert names[-2] == fault  # No cleanup call at callback level.
        else:
            assert names[-2] == 'radio_finalize'
        ap_cases.append(dict(input_channel=channel, injected_service_failure=fault,
                             initial_state=1, final_state=state, returned=returned,
                             channel_setup_arguments=selected, calls=calls,
                             notifications=notifications, stack_restored=True))
    return dict(stock_eg_sha256=sha256(data), host_mode_cases=host_cases,
                complete_ap_callback_cases=ap_cases,
                limitations=[
                    'Modes, profile index 7 and AP parameters are synthetic fixture inputs.',
                    'Host table dispatch executes; request delivery and callback registration are stubbed.',
                    'Host and embedded fixtures are independent, with no IPC handoff or UI binding.',
                    'Radio, security, IP and notification services are stubs; DHCP input flags are zero.',
                    'AP callback runs to its original notification and return; outer recovery is not executed.',
                    'The AP callback success does not prove the requested channel was applied.',
                    'No USB, persistent setting write, firmware patch, camera access, RF or throughput test.',
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
    print(f"Passed {len(report['host_mode_cases'])} host dispatch and "
          f"{len(report['complete_ap_callback_cases'])} complete AP callback offline cases")


if __name__ == '__main__':
    main()
