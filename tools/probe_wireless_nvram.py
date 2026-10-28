"""Execute stock NVRAM override registration and selection offline.

Unicorn executes the original request construction and selection branches.
Allocation, memory helpers and hardware services are bounded stubs. No camera
access; the 1 dB candidate is constructed only in memory for comparison.
"""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_band import machine
from probe_wireless_config import DATA, STACK, STOP, runtime
from build_wireless_power_trial import NVRAM_ROM, NVRAM_SIZE, build_trial
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256

OVERRIDE = 0xC12020D8
BUFFER = 0xC149D39C


def probe(data):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R7,
                                   UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
    StockImage(data)
    registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def result(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    registration = []
    for label, old_pointer, explicit in (
            ('stock_startup', 0, False),
            ('startup_preserves_existing_pointer', DATA + 0x7000, False),
            ('explicit_override', 0, True),
            ('explicit_replaces_existing_pointer', DATA + 0x7000, True)):
        u = machine(data)
        u.mem_map(0xC145F000, 0x2000)
        u.mem_write(OVERRIDE, struct.pack('<I', old_pointer))
        u.mem_write(DATA + 0x2000 + 8, struct.pack('<I', DATA + 0x3000))
        if explicit:
            payload = struct.pack('<I', 8) + b'a=b\0\0\0\0\0'
            u.mem_write(DATA, payload)
            for reg, value in zip(registers, (0, DATA, 12, 0)):
                u.reg_write(reg, value)
            u.mem_write(STACK, struct.pack('<3I', 1, 1, 0))
        captured = []

        def hook(emulator, address, size, context):
            r0, r1, r2, r3 = [emulator.reg_read(reg) for reg in registers]
            if address == runtime(0x11D5D8DE):
                result(emulator, DATA + 0x2000)
            elif address == runtime(0x11D6CDCC):
                emulator.mem_write(r0, bytes(emulator.mem_read(r1, r2)))
                result(emulator, r0)
            elif address == runtime(0x11D61EA4):
                request = struct.unpack('<5I', emulator.mem_read(r2, 20))
                captured.append(dict(command=request[0], buffer=hex(request[1]),
                                     length=request[2], output_capacity=request[3],
                                     flags=hex(request[4])))
            elif address == runtime(0x11D61EE2):
                emulator.emu_stop()

        for rom in (0x11D5D8DE, 0x11D6CDCC, 0x11D61EA4, 0x11D61EE2):
            address = runtime(rom)
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        entry = 0x11D5D9EC if explicit else 0x11D5DC10
        u.emu_start(runtime(entry) | 1, STOP + 2, count=3000)
        assert u.reg_read(UC_ARM_REG_PC) == runtime(0x11D61EE2)
        actual = struct.unpack('<I', u.mem_read(OVERRIDE, 4))[0]
        assert len(captured) == 1 and captured[0]['command'] == 0
        assert captured[0]['length'] == (12 if explicit else 0)
        assert actual == (DATA + 0x3000 if explicit else old_pointer)
        if explicit:
            assert bytes(u.mem_read(actual, 12)) == payload
        registration.append(dict(fixture=label, entry_rom=hex(entry),
                                 pointer_before=hex(old_pointer), pointer_after=hex(actual),
                                 requests=captured, stopped_before_hardware_setup=True))

    candidate, candidate_report = build_trial(data, 1.0)
    selections = []
    for image_name, image in (('stock', data), ('one_db_candidate', candidate)):
        for length in (None, 32, 1024, 1025):
            u = machine(image)
            u.mem_map(0xC149D000, 0x2000)
            context = DATA + 0x4000
            alternative = DATA + 0x5000
            payload = b'mcsbw202gpo=0xaaaaaaaa\0\0' if length == 32 else b'x' * (length or 0)
            if length is not None:
                payload = payload.ljust(length, b'\0')
                u.mem_write(alternative, struct.pack('<I', length) + payload)
            u.mem_write(OVERRIDE, struct.pack('<I', alternative if length is not None else 0))
            u.mem_write(context + 0x68, struct.pack('<I', BUFFER))
            u.mem_write(STACK + 0x10, struct.pack('<2I', runtime(NVRAM_ROM), NVRAM_SIZE))
            u.reg_write(UC_ARM_REG_R4, context)
            u.reg_write(UC_ARM_REG_R7, 1024)
            stop = []

            def select_hook(emulator, address, size, unused):
                r0, r1, r2, r3 = [emulator.reg_read(reg) for reg in registers]
                if address == runtime(0x11D6CEDC):
                    emulator.mem_write(r0, bytes(r1))
                    result(emulator, r0)
                elif address == runtime(0x11D6CC24):
                    emulator.mem_write(r0, bytes(emulator.mem_read(r1, r2)))
                    result(emulator, r0)
                elif address == runtime(0x11D4B424):
                    result(emulator)
                elif address == runtime(0x11D4BC5C):
                    result(emulator)
                elif address in (runtime(0x11D60094), runtime(0x11D6005C)):
                    stop.append('oversize_error' if address == runtime(0x11D6005C)
                                else 'after_selection')
                    emulator.emu_stop()

            for rom in (0x11D6CEDC, 0x11D6CC24, 0x11D4B424, 0x11D4BC5C,
                        0x11D60094, 0x11D6005C):
                address = runtime(rom)
                u.hook_add(UC_HOOK_CODE, select_hook, begin=address, end=address)
            u.emu_start(runtime(0x11D6002C) | 1, STOP + 2, count=1000)
            assert len(stop) == 1
            if length == 1025:
                assert stop == ['oversize_error']
                selected = dict(source='rejected', pointer=None, length=None, sha256=None)
            else:
                assert stop == ['after_selection']
                size, pointer = struct.unpack('<2I', u.mem_read(context + 0x64, 8))
                expected_size = NVRAM_SIZE if length is None else length + 2
                assert size == expected_size
                expected_pointer = runtime(NVRAM_ROM) if length is None else BUFFER
                assert pointer == expected_pointer
                raw = bytes(u.mem_read(pointer, size))
                if length is None:
                    assert raw == image[NVRAM_ROM - BASE:NVRAM_ROM - BASE + NVRAM_SIZE]
                else:
                    assert raw[:length] == payload
                selected = dict(source='builtin' if length is None else 'override',
                                pointer=hex(pointer), length=size, sha256=sha256(raw))
            selections.append(dict(image=image_name, override_payload_length=length,
                                   stopped_at=stop[0], selected=selected))
    for length in (32, 1024):
        matches = [c['selected']['sha256'] for c in selections if c['override_payload_length'] == length]
        assert len(set(matches)) == 1
    builtin = [c['selected']['sha256'] for c in selections if c['override_payload_length'] is None]
    assert len(set(builtin)) == 2
    return dict(stock_eg_sha256=sha256(data), registration_cases=registration,
                selection_cases=selections,
                in_memory_candidate_sha256=candidate_report['candidate_sha256'],
                limitations=[
                    'Default command-0 request reaches the original override check; hardware setup is not run.',
                    'Request allocation and memory-copy helpers are stubbed.',
                    'Selection starts mid-function with explicit context, callback results and override pointer.',
                    'Alternative payloads and retained-pointer scenarios are synthetic, not camera dumps.',
                    'No OTP contents, actual NVRAM upload, power, boot or throughput validation.',
                    'The additional two bytes in the override length are observed stock arithmetic, not a proven format contract.',
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
    print('Passed 4 override registration and 8 stock/candidate selection cases')


if __name__ == '__main__':
    main()
