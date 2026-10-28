"""Execute host wireless profile validation, lookup and request construction.

The persistent settings loader/notification and hardware entry points are stubs.
Profile IDs are synthetic; no geographic meaning or camera state is inferred.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256

REGION_INDEX = 0xC18807C0
VALIDATION_TABLE, PROFILE_TABLE = 0x114A3624, 0x114A37BC


def probe(data):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR
    StockImage(data)

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    lookup = []
    for index in list(range(34)) + [34, 0xFFFFFFFF]:
        context = Context(data, data)
        u = context.u
        u.mem_write(REGION_INDEX, struct.pack('<I', 0xFFFFFFFF))
        refreshes = []

        def hook(emulator, address, size, unused):
            if address == 0x101F4E06:
                refreshes.append(emulator.reg_read(UC_ARM_REG_R0))
                result(emulator, 1)
            elif address == 0x1008FB78:
                result(emulator)

        for address in (0x101F4E06, 0x1008FB78):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        setter_return = context.call(0x101F5088, (index,), limit=2000)
        stored_index = struct.unpack('<I', u.mem_read(REGION_INDEX, 4))[0]
        getter_return = context.call(0x101F5022, (RAM,), limit=2000)
        raw = bytes(u.mem_read(RAM, 12))
        valid = index in set(range(1, 11)) | set(range(14, 26))
        assert setter_return == int(valid)
        assert stored_index == (index if valid else 0xFFFFFFFF)
        effective = index if valid else 7
        expected = data[PROFILE_TABLE - BASE + effective * 12:PROFILE_TABLE - BASE + effective * 12 + 12]
        assert raw == expected and getter_return == 1 and refreshes == [0x47]
        country, revision, identifier = struct.unpack('<4sII', raw)
        lookup.append(dict(input_index=index, setter_return=setter_return,
                           stored_index=stored_index, effective_lookup_index=effective,
                           country=country.split(b'\0')[0].decode('ascii'),
                           revision=revision, profile_identifier=identifier))

    requests = []
    for index, mode in ((1, 0), (3, 0), (7, 0), (25, 0), (0xFFFFFFFF, 0), (7, 1)):
        context = Context(data, data)
        u = context.u
        context.map(0xC18CA000, 0x1000)
        u.mem_write(REGION_INDEX, struct.pack('<I', index))
        captured, getter_calls = [], []

        def request_hook(emulator, address, size, unused):
            r0, r1, r2 = [emulator.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            if address == 0x101F4E06:
                getter_calls.append(r0)
                result(emulator, 1)
            elif address == 0x10392F00:
                fields = struct.unpack('<6I', emulator.mem_read(r0, 24))
                country = (bytes(emulator.mem_read(fields[3], 3)).split(b'\0')[0].decode('ascii')
                           if fields[3] else None)
                captured.append(dict(first_enable=fields[0], second_enable=fields[1],
                                     mode_byte=fields[2] & 255, country=country,
                                     revision=fields[4], trailing_word=fields[5]))
                result(emulator, 1)
            else:
                result(emulator)

        for address in (0x10392E8E, 0x10392ED0, 0x101F4E06, 0x10392F00):
            u.hook_add(UC_HOOK_CODE, request_hook, begin=address, end=address)
        returned = context.call(0x103A72C8, (mode,), limit=3000)
        assert returned == 1 and len(captured) == 1
        request = captured[0]
        assert request['first_enable'] == 0 and request['second_enable'] == 1
        if mode == 0:
            assert getter_calls == [0x47]
            assert request['mode_byte'] == 1 and request['country'] == 'AA'
            assert request['revision'] == (7 if index == 0xFFFFFFFF else index)
        else:
            assert getter_calls == []
            assert request['mode_byte'] == 0 and request['country'] is None
            assert request['revision'] == 0xFFFFFFFF
        requests.append(dict(input_index=index, caller_mode=mode,
                             settings_refresh_calls=getter_calls, request=request, returned=returned))
    return dict(stock_eg_sha256=sha256(data), validation_table_rom=hex(VALIDATION_TABLE),
                profile_table_rom=hex(PROFILE_TABLE), cached_index_ram=hex(REGION_INDEX),
                lookup_cases=lookup, request_cases=requests,
                limitations=[
                    'All cached profile IDs and the settings-loader return are synthetic.',
                    'Settings refresh and persistence notification are stubs; no real persistent data is read or written.',
                    'Request construction executes stock code; final connection dispatch and callback registration are stubbed.',
                    'The zero-valued request trailing word reflects fixture stack contents, not a recovered initialization guarantee.',
                    'Profile numbers are not mapped to sales regions and do not prove actual device configuration.',
                    'No hardware, RF, connection or throughput validation.',
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
    print('Passed 36 profile validation/lookup and 6 request-construction offline cases')


if __name__ == '__main__':
    main()
