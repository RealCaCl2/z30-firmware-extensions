"""Execute bounded stock band-selection and AP-channel paths offline.

Requires Unicorn. Driver commands and unrelated initialization services are
stubbed. Optional instruction changes exist only inside the emulator; this tool
does not produce firmware or access a camera.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from probe_wireless_config import SOURCE, DESTINATION, SIZE, DATA, STACK, STOP, runtime


def machine(data):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
    u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
    u.mem_map(DATA, 0x10000)
    u.mem_map(0x60000000, 0x10000)
    u.reg_write(UC_ARM_REG_SP, STACK)
    u.reg_write(UC_ARM_REG_LR, STOP | 1)
    return u


def probe(data):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    StockImage(data)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def args(u):
        return [u.reg_read(r) for r in regs]

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def ioctl(u):
        r0, r1, r2, r3 = args(u)
        sp = u.reg_read(UC_ARM_REG_SP)
        setting, auxiliary, interface = struct.unpack('<3I', u.mem_read(sp, 12))
        return dict(command=r0, value=struct.unpack('<I', u.mem_read(r1, 4))[0],
                    input_size=r2, output_capacity=r3, setting=setting,
                    auxiliary=auxiliary, interface=interface)

    band_cases = []
    for band in (0, 1, 2, 3):
        for driver_status in (0, -23):
            u = machine(data)
            u.reg_write(UC_ARM_REG_R0, band)
            commands = []

            def hook(emulator, address, size, context):
                if address == STOP:
                    emulator.emu_stop()
                elif address == runtime(0x11D5D9EC):
                    commands.append(ioctl(emulator))
                    result(emulator, driver_status)
                elif address == runtime(0x11D4BC58):
                    result(emulator)

            for address in (STOP, runtime(0x11D5D9EC), runtime(0x11D4BC58)):
                u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
            u.emu_start(runtime(0x11D52A12) | 1, STOP + 2, count=1000)
            assert u.reg_read(UC_ARM_REG_PC) == STOP
            assert u.reg_read(UC_ARM_REG_SP) == STACK
            expected_return = 0 if driver_status == 0 else 0xFFFFFFFA
            assert u.reg_read(UC_ARM_REG_R0) == expected_return
            assert commands == [dict(command=142, value=band, input_size=4,
                                     output_capacity=4, setting=1, auxiliary=0,
                                     interface=0)]
            band_cases.append(dict(band=band, synthetic_driver_status=driver_status,
                                   returned=expected_return, stack_restored=True,
                                   commands=commands))

    init_cases = []
    # Execute the stock initializer through the band ioctl, stopping before the
    # common IP-service setup. Preserve all stock instructions except the single
    # explicit in-emulator alternative in the last two fixtures.
    for mode, replacement, driver_status in (
            (0, None, 0), (1, None, 0), (1, None, -23),
            (1, 0, 0), (1, 1, 0)):
        u = machine(data)
        u.mem_write(DATA, struct.pack('<2I', 0, mode))
        u.reg_write(UC_ARM_REG_R1, DATA)
        for offset in (0x24, 0x28, 0x30):
            u.mem_write(0xC12007EC + offset, struct.pack('<I', 1))
        if replacement is not None:
            assert bytes(u.mem_read(runtime(0x11D0FBC4), 2)) == b'\x02\x20'
            u.mem_write(runtime(0x11D0FBC4), bytes((replacement, 0x20)))
        calls, commands, stopped = [], [], []
        stubs = {0x11D4F058: 'radio_init', 0x11D53BE0: 'service_setup',
                 0x11D6CB04: 'strlen', 0x11D4F75A: 'country_setup',
                 0x11D531DE: 'vht_config', 0x11D504FE: 'station_setup',
                 0x11D50704: 'nmode_setup', 0x11D5186E: 'ap_setup'}

        def init_hook(emulator, address, size, context):
            if address in (runtime(0x11D0FC12), runtime(0x11D0FC62)):
                stopped.append('common_setup' if address == runtime(0x11D0FC12)
                               else 'failure_cleanup')
                emulator.emu_stop()
            elif address == runtime(0x11D5D9EC):
                commands.append(ioctl(emulator))
                result(emulator, driver_status)
            elif address == runtime(0x11D4BC58):
                result(emulator)
            else:
                rom = address - DESTINATION + SOURCE
                if rom in stubs:
                    calls.append(dict(service=stubs[rom], arguments=args(emulator)))
                    result(emulator, 2 if stubs[rom] == 'strlen' else 0)

        addresses = list(stubs) + [0x11D0FC12, 0x11D0FC62, 0x11D5D9EC, 0x11D4BC58]
        for rom in addresses:
            address = runtime(rom)
            u.hook_add(UC_HOOK_CODE, init_hook, begin=address, end=address)
        u.emu_start(runtime(0x11D0FB38) | 1, STOP + 2, count=3000)
        assert len(stopped) == 1
        expected_band = 2 if replacement is None else replacement
        assert [c['value'] for c in commands] == ([expected_band] if mode else [])
        assert all(c['command'] == 142 for c in commands)
        selector = bytes(u.mem_read(0xC1201EEC, 1))[0]
        assert selector == (2 if mode else 1)
        assert stopped == (['failure_cleanup'] if driver_status else ['common_setup'])
        init_cases.append(dict(mode_argument=mode, in_emulator_band_replacement=replacement,
                               synthetic_driver_status=driver_status, selected_image=selector,
                               stopped_at=stopped[0], commands=commands, stubbed_calls=calls))

    channel_cases = []
    for channel in (0, 1, 6, 11, 12, 36, 149):
        u = machine(data)
        config = DATA + 0x100
        u.mem_write(DATA, struct.pack('<2I', 0, config))
        u.mem_write(config, bytes((channel,)))
        u.mem_write(config + 8, b'fixture\0')
        u.reg_write(UC_ARM_REG_R1, DATA)
        calls, stopped = [], []
        stubs = {0x11D6CB04: 'strlen', 0x11D513EE: 'ap_mode_setup',
                 0x11D50F62: 'ssid_setup', 0x11D51256: 'channel_setup',
                 0x11D52BDE: 'auto_channel_control', 0x11D5125E: 'channel_query'}

        def channel_hook(emulator, address, size, context):
            if address == runtime(0x11D0FFC4):
                stopped.append('after_channel_selection')
                emulator.emu_stop()
                return
            rom = address - DESTINATION + SOURCE
            name = stubs[rom]
            r0, r1, r2, r3 = args(emulator)
            if name == 'channel_query':
                emulator.mem_write(r0, struct.pack('<H', 0x1006))
            calls.append(dict(service=name, arguments=[r0, r1, r2, r3]))
            result(emulator, 7 if name == 'strlen' else 0)

        for rom in list(stubs) + [0x11D0FFC4]:
            address = runtime(rom)
            u.hook_add(UC_HOOK_CODE, channel_hook, begin=address, end=address)
        u.emu_start(runtime(0x11D0FEC6) | 1, STOP + 2, count=3000)
        assert stopped == ['after_channel_selection']
        selected = [c['arguments'][0] for c in calls if c['service'] == 'channel_setup']
        expected = [0] if channel == 0 else [0x1000 | channel] if channel <= 11 else []
        assert selected == expected, (channel, selected, expected)
        channel_cases.append(dict(input_channel=channel, channel_setup_arguments=selected,
                                  stopped_at=stopped[0], stubbed_calls=calls))

    profile_cases = []
    # The stock country callback also accepts a special AA + numeric profile
    # input. Do not infer geographic meaning for those profile numbers.
    for country, revision in [(b'AA', n) for n in range(27)] + [(b'CN', 0), (b'US', -1)]:
        u = machine(data)
        u.mem_write(DATA, struct.pack('<I4sI', 0, country, revision & 0xFFFFFFFF))
        u.reg_write(UC_ARM_REG_R1, DATA)
        notifications = []

        def string(address):
            return bytes(u.mem_read(address, 64)).split(b'\0')[0]

        def profile_hook(emulator, address, size, context):
            r0, r1, r2, r3 = args(emulator)
            if address == STOP:
                emulator.emu_stop()
            elif address == runtime(0x11D6CFDC):
                left, right = string(r0), string(r1)
                result(emulator, (left > right) - (left < right))
            elif address == runtime(0x11D6CA88):
                emulator.mem_write(r0, string(r1) + b'\0')
                result(emulator, r0)
            elif address == runtime(0x11D1791E):
                pointer, length = struct.unpack('<2I', emulator.mem_read(r3, 8))
                status = struct.unpack('<H', emulator.mem_read(pointer, 2))[0]
                notifications.append(dict(group=r0, command=r1, count=r2,
                                          length=length, status=status))
                result(emulator)

        for address in (STOP, runtime(0x11D6CFDC), runtime(0x11D6CA88), runtime(0x11D1791E)):
            u.hook_add(UC_HOOK_CODE, profile_hook, begin=address, end=address)
        u.emu_start(runtime(0x11D106DC) | 1, STOP + 2, count=3000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP
        assert u.reg_read(UC_ARM_REG_SP) == STACK
        assert len(notifications) == 1
        selected_country = bytes(u.mem_read(0xC1200808, 4)).split(b'\0')[0].decode('ascii')
        selected_revision = struct.unpack('<i', u.mem_read(0xC120080C, 4))[0]
        band, mask = struct.unpack('<2I', u.mem_read(0xC1201F2C, 8))
        query_flag = bytes(u.mem_read(0xC12007EC, 1))[0]
        invalid = country == b'AA' and revision in (0, 11, 12, 13, 26)
        assert notifications[0]['status'] == int(invalid)
        if country == b'AA':
            assert selected_country == ('US' if invalid else 'XT')
            assert (selected_revision == -1) if invalid else (80 <= selected_revision <= 89)
            assert ((band, mask) == (0, 0)) if invalid else (band == 1 and mask in (1, 3, 7))
        else:
            assert selected_country == country.decode('ascii') and selected_revision == revision
            assert (band, mask) == (1, 7) and query_flag == 1
        profile_cases.append(dict(input_country=country.decode('ascii'), input_revision=revision,
                                  stored_country=selected_country, stored_revision=selected_revision,
                                  bandwidth_band=band, bandwidth_mask=mask,
                                  cached_vht_flag=query_flag, notifications=notifications,
                                  stack_restored=True))

    vht_cases = []
    for flag in (0, 1):
        u = machine(data)
        u.mem_write(DATA, bytes((flag,)))
        u.reg_write(UC_ARM_REG_R0, 1)
        u.reg_write(UC_ARM_REG_R1, DATA)
        calls = []

        def vht_hook(emulator, address, size, context):
            r0, r1, r2, r3 = args(emulator)
            if address == STOP:
                emulator.emu_stop()
            elif address == runtime(0x11D5FD48):
                emulator.mem_write(r0, b'\0\0')
                result(emulator)
            elif address == runtime(0x11D5D9EC):
                assert r0 == 162  # Synthetic interface is already down.
                emulator.mem_write(r1, bytes(r2))
                result(emulator)
            elif address == runtime(0x11D5FA36):
                calls.append(dict(value=struct.unpack('<I', emulator.mem_read(r0, 4))[0],
                                  setting=r1, interface=r2, bss=r3))
                result(emulator)

        for address in (STOP, runtime(0x11D5FD48), runtime(0x11D5D9EC), runtime(0x11D5FA36)):
            u.hook_add(UC_HOOK_CODE, vht_hook, begin=address, end=address)
        u.emu_start(runtime(0x11D531DE) | 1, STOP + 2, count=3000)
        assert u.reg_read(UC_ARM_REG_PC) == STOP
        assert u.reg_read(UC_ARM_REG_SP) == STACK
        assert calls == [dict(value=flag, setting=1, interface=0, bss=0)]
        vht_cases.append(dict(input_flag=flag, low_wrapper_calls=calls, stack_restored=True))

    pointers = [struct.unpack_from('<I', data, SOURCE - BASE + (address - DESTINATION))[0]
                for address in (0xC1202164, 0xC120216C, 0xC1202168)]
    assert pointers == [0xC10E7974, 0xC116C608, 0]
    return dict(stock_eg_sha256=sha256(data), band_cases=band_cases,
                initialization_cases=init_cases, ap_channel_cases=channel_cases,
                country_profile_cases=profile_cases,
                vht_set_cases=vht_cases,
                firmware_selector_pointers=dict(zip(('1', '2', '3'), map(hex, pointers))),
                limitations=[
                    'No radio, SDIO, OTP, active country, RF or throughput measurement.',
                    'Band ioctl driver returns are synthetic and do not prove acceptance.',
                    'Initialization and channel probes stop at explicit mid-function boundaries.',
                    'The two instruction changes are in emulator memory only, not firmware artifacts.',
                    'AP channel services are stubs; the probe checks caller selection and encoding only.',
                    'Mode argument is recovered from stock branching, not tied to a camera UI choice.',
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
    print('Passed 8 band-wrapper, 5 initialization, 7 AP-channel, 29 country-profile and 2 VHT offline cases')


if __name__ == '__main__':
    main()
