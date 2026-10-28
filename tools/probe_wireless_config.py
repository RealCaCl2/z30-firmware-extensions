"""Execute stock wireless wrappers offline, stopping at a stubbed driver command.

Requires Unicorn. Uses the embedded ARM image's RAM mapping, not its ROM address.
Allocator, string/memory helpers and driver commands are stubs. No camera access.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256

SOURCE, DESTINATION, SIZE = 0x11C9E69C, 0xC1000000, 0x204A00
DATA, STACK, STOP = 0x50000000, 0x60008000, 0x6000F000
WRAPPERS = {
    'country': (0x11D5E110, b'US\0\0\0\0\0\0US\0\0'),
    'nmode': (0x11D5E6F2, struct.pack('<i', -1)),
    'amsdu': (0x11D5E778, struct.pack('<I', 1)),
    'bw_cap': (0x11D5F28E, struct.pack('<2I', 1, 1)),
    'ampdu_ba_wsize': (0x11D5F2BA, struct.pack('<I', 16)),
    'ampdu_mpdu': (0x11D5F2D4, struct.pack('<I', 16)),
    'vhtmode': (0x11D5FA36, struct.pack('<I', 1)),
}


def runtime(rom):
    return DESTINATION + rom - SOURCE


def probe(data):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC)
    StockImage(data)
    assert struct.unpack_from('<I', data, 0x11EA309C - BASE)[0] == SIZE
    assert struct.unpack_from('<I', data, SOURCE - BASE)[0] == 0xE59FF018
    assert struct.unpack_from('<I', data, SOURCE - BASE + 0x20)[0] == DESTINATION + 0x60
    registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
    results = []
    for name, (entry, value) in WRAPPERS.items():
        for setting in (False, True):
            u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
            u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
            u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
            u.mem_map(DATA, 0x10000)
            u.mem_map(0x60000000, 0x10000)
            u.mem_write(DATA, value)
            u.mem_write(STOP, b'\x00\xbe')
            u.reg_write(UC_ARM_REG_SP, STACK)
            u.reg_write(UC_ARM_REG_LR, STOP | 1)
            args = (DATA, len(value), int(setting), 1) if name == 'country' else (
                DATA, int(setting), 0, 0)
            for register, arg in zip(registers, args):
                u.reg_write(register, arg)
            u.mem_write(STACK, bytes(16))
            commands = []
            heap = DATA + 0x1000

            def read_string(address):
                raw = bytearray()
                for offset in range(256):
                    byte = bytes(u.mem_read(address + offset, 1))
                    if byte == b'\0':
                        return bytes(raw)
                    raw.extend(byte)
                raise ValueError('Unterminated fixture string')

            def hook(emulator, address, size, context):
                nonlocal heap
                if address == STOP:
                    emulator.emu_stop()
                    return
                r0, r1, r2, r3 = [emulator.reg_read(r) for r in registers]
                if address == runtime(0x11D6CB04):
                    result = len(read_string(r0))
                elif address == runtime(0x11D5D8DE):
                    result = heap
                    heap += (r0 + 15) & ~15
                    assert heap < DATA + 0x10000
                elif address == runtime(0x11D5D968):
                    result = 0
                elif address == runtime(0x11D6CC24):
                    emulator.mem_write(r0, bytes(emulator.mem_read(r1, r2)))
                    result = r0
                elif address == runtime(0x11D5D9EC):
                    payload = bytes(emulator.mem_read(r1, r2))
                    key, content = payload.split(b'\0', 1)
                    commands.append({'command': r0, 'name': key.decode('ascii'),
                                     'payload_size': r2, 'value_hex': content.hex(),
                                     'output_capacity': r3})
                    # Synthetic get result; no claim about device settings.
                    if not setting:
                        emulator.mem_write(r1, bytes(r2))
                    result = 0
                else:
                    return
                emulator.reg_write(UC_ARM_REG_R0, result)
                emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

            hooks = [STOP] + [runtime(a) for a in (
                0x11D6CB04, 0x11D5D8DE, 0x11D5D968, 0x11D6CC24, 0x11D5D9EC)]
            for address in hooks:
                u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
            u.emu_start(runtime(entry) | 1, STOP + 2, count=10000)
            assert u.reg_read(UC_ARM_REG_PC) == STOP
            assert u.reg_read(UC_ARM_REG_SP) == STACK
            assert u.reg_read(UC_ARM_REG_R0) == 0
            assert len(commands) == 1
            command = commands[0]
            assert command['command'] == (263 if setting else 262)
            assert command['name'] == name
            assert command['value_hex'] == value.hex()
            results.append({'wrapper': name, 'operation': 'set' if setting else 'get',
                            'rom_entry': hex(entry), 'runtime_entry': hex(runtime(entry)),
                            'stack_restored': True, **command})
    return {'stock_eg_sha256': sha256(data), 'cases': results,
            'limitations': [
                'Synthetic arguments; no active settings or throughput measured.',
                'Allocator, strlen, memcpy, free and bottom driver command are stubbed.',
                'Does not validate driver acceptance, concurrency, RF, SDIO or hardware.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Passed {len(report['cases'])} offline stock wrapper cases")


if __name__ == '__main__':
    main()
