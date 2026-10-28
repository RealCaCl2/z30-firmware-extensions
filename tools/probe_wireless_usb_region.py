"""Offline execution of the C1.20 service envelope's region-read path.

No service mode is entered. Settings access and USB transmission are fixtures;
the dispatcher, gate, table lookup and region getters execute stock code.
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
                                   UC_ARM_REG_PC, UC_ARM_REG_LR)
    stock = StockImage(data)
    assert stock.words(0x115CE22C, 7) == (0x94FF, 1, 0, 0, 0x1059FAD5, 0x1059FADB, 0x1059FAAB)
    assert stock.words(0x115D0404, 7) == (0xF006, 1, 0, 0, 0x10307BFD, 0x10307C01, 0x10307BFB)
    cases = []
    for gate, region, profile, count in ((0, 2, 7, 5), (1, 2, 7, 5),
                                         (1, 4, 3, 5), (1, 2, 7, 4)):
        context = Context(data, data)
        u = context.u
        context.map(0xC18CA000, 0x1000)
        context.map(0xC29B0000, 0x1000)
        u.mem_write(0xC18CAD24, struct.pack('<2I', 0, 0x1030765D))
        u.mem_write(0xC18CAD2C, struct.pack('<I', gate))
        request, buffer, settings = RAM, RAM + 0x100, RAM + 0x200
        params = (0xF006, 0, 0, 0, 0)[:count]
        u.mem_write(request, struct.pack('<IHHI', 12 + count * 4, 1, 0x94FF, 1)
                    + struct.pack('<' + 'I' * count, *params))
        u.mem_write(buffer, b'\xa5' * 16)
        u.mem_write(settings, struct.pack('<2I', region, profile))
        reads, payloads, responses = [], [], []

        def ret(value=0):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def hook(emulator, address, size, unused):
            a, b, c = [emulator.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            if address == 0x10212238:
                reads.append(a)
                emulator.mem_write(b + 4, struct.pack('<I', settings))
                ret(1)
            elif address == 0x1045AD3C:
                ret(buffer)
            elif address == 0x1045AF5A:
                payloads.append(bytes(emulator.mem_read(b, c)).hex())
                ret(1)
            elif address == 0x1045AEB4:
                responses.append(b)
                ret(1)

        for address in (0x10212238, 0x1045AD3C, 0x1045AF5A, 0x1045AEB4):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        validated = context.call(0x1059FAD4, (0, request), limit=3000)
        if gate and count == 5:
            assert validated == 1 and responses == [] and reads == []
            returned = context.call(0x1059FADA, (0, request), limit=3000)
            assert returned == 1 and reads == [0x47, 0x47]
            assert payloads == [bytes((region, profile, 0xA5, 0xA5)).hex()]
            assert responses == [0x2001]
        else:
            returned = None
            assert validated == 0 and not reads and not payloads
            assert responses == [0xA004 if gate == 0 else 0x2006]
        cases.append(dict(service_gate=gate, synthetic_region=region,
                          synthetic_profile=profile, parameter_count=count,
                          validated=validated, handler_return=returned,
                          settings_slots_read=reads, payloads=payloads,
                          response_codes=[f'0x{v:04x}' for v in responses]))
    return dict(stock_eg_sha256=sha256(data), cases=cases,
                limitations=['Settings slot 0x47 and gate values are synthetic.',
                             'USB transport and persistent settings lookup are stubs.',
                             'The four-byte read payload only explicitly assigns its first two bytes.',
                             'No camera state, region write, radio state or throughput is validated.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Passed four offline USB region-read route cases')


if __name__ == '__main__':
    main()
