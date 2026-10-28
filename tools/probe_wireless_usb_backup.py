"""Offline contract for service F02D block-8 backup and F02E restoration.

Runs stock table dispatch, range checks, settings descriptor lookup and copy.
Backend metadata, USB and persistence are fixtures. Never opens a USB device.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256


def probe(data):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_PC, UC_ARM_REG_LR)
    stock = StockImage(data)
    assert stock.words(0x115D0538, 7) == (0xF02D, 1, 0, 0, 0x10330E7F, 0x10330ED1, 0x10330E7D)
    assert stock.words(0x115D0554, 7) == (0xF02E, 0, 0, 0x103306AF, 0x10330F3B, 0x10330F8D, 0x10330E7D)
    size, storage = 0x1FFFC, 0xC20B4248
    baseline = bytes((i * 37 + 11) & 255 for i in range(size))
    definitions = [('read', 0, size, 1), ('read', 0x351, 1, 1),
                   ('read', size - 1, 1, 1), ('read', 0, 0, 1),
                   ('read', size, 1, 1), ('read', 0x351, 1, 0),
                   ('restore', 0x351, 1, 1), ('restore', 0x351, 1, 0)]
    cases = []
    for operation, offset, length, service_result in definitions:
        context = Context(data, data)
        u = context.u
        context.map(0xC18CA000, 0x1000)
        context.map(storage - 4, size + 8)
        u.mem_write(storage - 4, b'\xa5' * (size + 8))
        u.mem_write(storage, baseline)
        # Execute the stock descriptor initialization for block 8. Allocation
        # and other slots are outside this bounded block.
        u.reg_write(UC_ARM_REG_R5, 0xC188087C)
        context.block(0x1008F9D6, 0x1008F9E8)
        descriptor = bytes(u.mem_read(0xC1880E0C, 28))
        assert struct.unpack_from('<2I', descriptor, 8) == (storage, size)
        u.mem_write(0xC18CAD24, struct.pack('<2I', 0, 0x1030765D))
        u.mem_write(0xC18CAD2C, struct.pack('<I', 1))  # Synthetic open gate.
        command = 0xF02D if operation == 'read' else 0xF02E
        u.mem_write(RAM, struct.pack('<IHHI5I', 32, 1, 0x94FF if operation == 'read' else 0x94FE,
                                    1, command, offset, length, 0, 0))
        metadata, payload = RAM + 0x400, RAM + 0x500
        u.mem_write(metadata, struct.pack('<3I', storage, 0, size))
        u.mem_write(payload, b'\x7e')
        reads, transfers, persists, responses = [], [], [], []

        def ret(value=0):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def hook(emulator, address, count, unused):
            a, b, c, d = [emulator.reg_read(r) for r in
                           (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
            if address == 0x1008FC18:
                reads.append(a)
                assert a == 8
                ret(0 if operation == 'read' and service_result == 0 else metadata)
            elif address == 0x1045AE26:
                actual = bytes(emulator.mem_read(b, c))
                assert actual == baseline[offset:offset + length]
                transfers.append({'length': c, 'sha256': sha256(actual)})
                ret(1)
            elif address == 0x1045AD3C:
                ret(payload)
            elif address == 0x103307EC:
                persists.append(a)
                assert a == 8
                ret(service_result)
            elif address == 0x1045AEB4:
                assert b == 0x2001 and c == 5
                responses.append([hex(v) for v in struct.unpack('<5I', emulator.mem_read(d, 20))])
                ret(1)

        for address in (0x1008FC18, 0x1045AE26, 0x1045AD3C, 0x103307EC, 0x1045AEB4):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        validate = 0x1059FAD4 if operation == 'read' else 0x1059FAC8
        execute = 0x1059FADA if operation == 'read' else 0x1059FACE
        validated = context.call(validate, (0, RAM), limit=4000)
        valid_range = length > 0 and offset <= size and length <= size - offset
        expected_valid = valid_range and not (operation == 'read' and service_result == 0)
        assert validated == int(expected_valid)
        returned = context.call(execute, (0, RAM), limit=4000) if validated else None
        current = bytes(u.mem_read(storage, size))
        if operation == 'read':
            assert current == baseline and not persists
            assert bool(transfers) == expected_valid
            assert responses[0][0] == ('0xa001' if service_result == 0 else ('0x2001' if valid_range else '0x201d'))
        else:
            assert returned == service_result and persists == [8]
            assert current == baseline[:offset] + b'\x7e' + baseline[offset + 1:]
            assert responses[0][0] == ('0x2001' if service_result else '0xa001')
        assert bytes(u.mem_read(storage - 4, 4)) == b'\xa5' * 4
        assert bytes(u.mem_read(storage + size, 4)) == b'\xa5' * 4
        cases.append(dict(operation=operation, offset=offset, length=length,
                          fixture_service_result=service_result, validated=validated,
                          returned=returned, metadata_reads=reads, transfers=transfers,
                          persistence_requests=persists, response_parameters=responses,
                          ram_changed=current != baseline))
    return dict(stock_eg_sha256=sha256(data), block=8, size=size, cases=cases,
                limitations=['Gate, RAM contents and backend metadata are synthetic.',
                             'Stock block-8 descriptor initialization and lookup execute; backend selection is stubbed.',
                             'USB transmission and settings persistence are stubs; no actual camera backup exists.',
                             'Restore failure leaves the RAM copy changed in this fixture; persistent storage is not modeled.',
                             'Block 8 does not include all other settings slots, including region slot 0x47.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Passed eight offline block-8 USB backup/restore contract cases')


if __name__ == '__main__':
    main()
