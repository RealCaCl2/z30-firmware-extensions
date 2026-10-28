"""Audit the stock F032 memory-read route offline, with fixed ordinary RAM/ROM.

Executes the service gate, original memory-type initializer/classifier, and
original USB payload memory copy. Transport scheduling/waits are synthetic.
Never connects to a camera or writes a firmware artifact.
"""
import argparse
import json
import struct
from pathlib import Path

from probe_wireless_config import SOURCE, DESTINATION, SIZE
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256


def probe(data):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR)
    stock = StockImage(data)
    assert stock.words(0x115D05C4, 7) == (0xF032, 1, 0, 0, 0x10331251, 0x10331345, 0x1033124F)
    definitions = [(1, BASE, 16, 5, True),
                   (1, DESTINATION, 64, 5, True),
                   (1, 0xC12007EC, 36, 5, True),
                   (1, 0xC1201F2C, 8, 5, True),
                   (1, 0xC12020D4, 8, 5, True),
                   (1, 0xC1202164, 12, 5, True),
                   (0, BASE, 16, 5, True),
                   (1, BASE, 0, 5, True),
                   (1, BASE, 16, 4, True),
                   (1, BASE, 16, 5, False)]
    cases = []
    for gate, address, length, count, transfer_success in definitions:
        context = Context(data, data)
        u = context.u
        context.map(DESTINATION, SIZE)
        u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
        context.map(0xC2A1BDF4, 0x200)
        context.call(0x10218350, limit=2000)  # Original address-region table.
        descriptor = context.call(0x102185D4, (address,), limit=2000)
        mapping = list(struct.unpack('<4I', u.mem_read(descriptor, 16)))
        assert context.call(0x102185AC, (address,), limit=2000) == 0
        u.mem_write(0xC18CAD24, struct.pack('<2I', 0, 0x1030765D))
        u.mem_write(0xC18CAD2C, struct.pack('<I', gate))
        params = (0xF032, address, length, 0, 0)[:count]
        u.mem_write(RAM, struct.pack('<IHHI', 12 + count * 4, 1, 0x94FF, 1)
                    + struct.pack('<' + 'I' * count, *params))
        buffer, packet = RAM + 0x1000, RAM + 0x2000
        u.mem_write(0xC18CA8D4 + 0x10, struct.pack('<I', buffer))
        before = bytes(u.mem_read(address, length))
        payloads, responses = [], []

        def result(value=0):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def hook(emulator, entry, size, unused):
            a, b, c, d = [emulator.reg_read(r) for r in
                           (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
            if entry == 0x1045AD6C:
                result(0x100)  # Larger than each fixed read and its header.
            elif entry == 0x1045AE16:
                assert b == length + 12 and c == buffer
                emulator.mem_write(c, bytes(12))  # USB header is out of scope.
                result(1)
            elif entry == 0x1031DE5A:
                result(packet)
            elif entry == 0x1031DE76:
                n, p = struct.unpack('<2I', emulator.mem_read(packet + 12, 8))
                assert p == buffer and n == length + 12
                actual = bytes(emulator.mem_read(p + 12, n - 12))
                assert actual == before
                payloads.append(dict(length=len(actual), sha256=sha256(actual)))
                result(1)
            elif entry == 0x1045ADCA:
                result(int(transfer_success))
            elif entry == 0x1045AEB4:
                assert c == 5
                responses.append(dict(code=hex(b), parameters=[hex(v) for v in
                                      struct.unpack('<5I', emulator.mem_read(d, 20))]))
                result(1)
            else:
                result()  # Event clear and transport cache synchronization.

        for entry in (0x1045AD6C, 0x1045AE16, 0x1031DE5A, 0x1031DE76,
                      0x1045ADCA, 0x1045AEB4, 0x103488FC, 0x10336474):
            u.hook_add(UC_HOOK_CODE, hook, begin=entry, end=entry)
        validated = context.call(0x1059FAD4, (0, RAM), limit=4000)
        supported = gate and length and count == 5
        assert validated == int(bool(supported))
        returned = context.call(0x1059FADA, (0, RAM), limit=6000) if validated else None
        assert bytes(u.mem_read(address, length)) == before
        if supported:
            assert len(payloads) == 1 and returned == int(transfer_success)
            assert responses == [dict(code='0x2001', parameters=[
                '0x2001' if transfer_success else '0x2007', '0x0', '0x0', '0x0', '0x0'])]
        else:
            assert not payloads
            assert responses[0]['code'] == ('0xa004' if not gate else
                                             '0x2006' if count != 5 else '0x2001')
            if gate and count == 5 and length == 0:
                assert responses[0]['parameters'][0] == '0x201d'
        cases.append(dict(service_gate=gate, address=hex(address), length=length,
                          parameter_count=count, injected_transfer_success=transfer_success,
                          original_region_descriptor=[hex(v) for v in mapping],
                          validated=validated, returned=returned,
                          payloads=payloads, responses=responses, source_unchanged=True))
    return dict(stock_eg_sha256=sha256(data), cases=cases,
                limitations=[
                    'The stock region table, classifier, handler and payload copy execute.',
                    'RAM contains the stock embedded image, not running camera state.',
                    'Gate, USB header, transport descriptor, queue, cache synchronization and waits are fixtures.',
                    'Fixed reads stay within declared ROM or embedded RAM regions; MMIO is not accessed.',
                    'F032 itself only checks nonzero length; host code must restrict exact addresses and lengths.',
                    'No cache coherence, live radio state, physical transfer, RF or throughput proof.',
                ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = probe(extract_eg(options.source.read_bytes()))
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f"Passed {len(report['cases'])} fixed memory-read route offline cases")


if __name__ == '__main__':
    main()
