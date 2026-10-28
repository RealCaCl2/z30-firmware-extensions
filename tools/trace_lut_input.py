"""Bounded native JPEG source selection; codec output is synthetic, not decoded."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM, STACK
from trace_lut_retouch import word, put, returning, image_descriptor
from trace_lut_buffers import registers
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP, UC_ARM_REG_PC


def save_input_binding(data):
    """Execute a stock caller suffix, stop before submission, then run initializer."""
    c = Context(data, data)
    c.map(0xC295BD18, 0x400)
    u = c.u
    request, image, metadata, options = [RAM + n for n in (0x1000, 0x2000, 0x3000, 0x7000)]
    sp = STACK + 0xE0000
    calls = []

    def boundary(uc, address, size, unused):
        if address == 0x1038F64A:
            calls.append(registers(uc, 4))
            uc.emu_stop()

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for width, height, stride in ((32, 16, 64), (5568, 3712, 5568)):
        for operation in (5, 6, 7):
            u.mem_write(sp, bytes(0x140))
            u.mem_write(request, bytes(0x60))
            put(u, request + 0x2C, options)
            u.mem_write(request + 9, bytes([operation]))
            put(u, sp + 0xD0, image)
            y, chroma = RAM + 0x20000, RAM + 0x30000
            put(u, sp + 0x40, y)
            put(u, sp + 0x44, chroma)
            for offset, value in ((0x4E, stride), (0x50, height), (0x52, width), (0x54, height)):
                u.mem_write(sp + offset, struct.pack('<H', value))
            for reg, value in ((UC_ARM_REG_R4, request), (UC_ARM_REG_R5, 1),
                               (UC_ARM_REG_R6, 0), (UC_ARM_REG_SP, sp)):
                u.reg_write(reg, value)
            calls.clear()
            u.emu_start(0x103450DD, 0x1038F64C, count=1000)
            assert u.reg_read(UC_ARM_REG_PC) == 0x1038F64A
            desc = sp + 0xF0
            assert calls == [[image, desc, 0, operation]]
            assert word(u, sp) == options
            assert list(struct.unpack('<2H', bytes(u.mem_read(desc + 2, 4)))) == [stride, stride]
            assert list(struct.unpack('<2H', bytes(u.mem_read(desc + 0xA, 4)))) == [width, height]
            assert [word(u, desc + x) for x in (0x14, 0x18)] == [y, chroma]
            put(u, 0xC295BD18, image)
            put(u, image + 0x3A4, metadata)
            u.mem_write(metadata + 0x2658, bytes([operation]))
            c.call(0x1038F91E, (0xC295BD18, desc, options))
            params = word(u, 0xC295BD18 + 0xC)
            assert word(u, params + 4) == desc
            rows.append(dict(dimensions=[width, height], stride=stride, operation=operation,
                             caller_arguments=calls[0], effect_input=hex(word(u, params + 4)),
                             input_planes=[hex(y), hex(chroma)],
                             scope='Caller suffix stops before submission; initializer returns fully. No plane preparation.'))
    return rows


def selection(data):
    c = Context(data, data)
    u = c.u
    job, image, owner, dest, replacement, metadata = [RAM + n for n in
        (0x1000, 0x6000, 0x7000, 0x8000, 0x9000, 0xA000)]
    y, chroma = RAM + 0x20000, RAM + 0x30000
    # Selection flag -> descriptor offset, established by stock branches.
    slots = {8: 0x40, 0x14: 0x7C, 0x18: 0x90, 0x1C: 0xA4,
             0x20: 0xB8, 0x24: 0xCC, 0x28: 0xE0}
    scenarios = [
        (0, [8], 32, 16, 0x40, False),
        (0, [0x20], 32, 16, 0xB8, False),
        (1, [8, 0x14], 32, 16, 0x7C, False),
        (1, [8, 0x24], 32, 16, 0xCC, False),
        (1, [8], 32, 16, 0x40, False),
        (2, [8, 0x18], 32, 16, 0x90, False),
        (2, [8, 0x28], 32, 16, 0xE0, False),
        (2, [8], 32, 16, 0x40, False),
        (3, [8, 0x1C], 32, 16, 0xA4, False),
        (4, [8, 0x14], 32, 16, 0x7C, False),
        (4, [8, 0x18], 1280, 720, 0x90, False),
        (4, [8], 5568, 3712, 0x40, False),
        # Small target with only full-size main image: replacement is allocated.
        (4, [8], 32, 16, 0x40, True),
    ]
    events = []
    failed = False

    def boundary(uc, address, size, unused):
        if address == 0x10143A30:
            events.append(dict(kind='image_allocation_replaced', arguments=registers(uc, 2)))
            returning(uc, replacement)
        elif address == 0x10096F88:
            events.append(dict(kind='native_decode_wrapper', arguments=registers(uc, 3)))
        elif address == 0x102194BC:
            config = registers(uc, 1)[0]
            events.append(dict(kind='codec_replaced', compressed=word(uc, config + 0x10),
                               planes=[word(uc, config + x) for x in (0x24, 0x28)]))
            uc.mem_write(config + 0x2C, bytes([failed]))
            uc.mem_write(config + 0x2E, struct.pack('<2H', 32, 16))
            returning(uc)
        elif address == 0x100A4DEE:
            events.append(dict(kind='decode_wrapper_return', result=registers(uc, 1)[0]))

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for mode, flags, width, height, selected, allocate in scenarios:
        for failed in (False, True):
            u.mem_write(job, bytes(0x4740))
            u.mem_write(image, bytes(0x400))
            u.mem_write(owner, bytes(0x100))
            put(u, job + 0xC, image)
            put(u, image + 0x320, owner)
            put(u, image + 0x3A4, metadata)
            put(u, job + 0x4608, dest)
            u.mem_write(job + 0x4604, bytes([mode]))
            image_descriptor(u, dest, 1, 64, width, height, y, chroma)
            image_descriptor(u, replacement + 0x18, 1, 64, width, height, y + 0x1000, chroma + 0x1000)
            for flag in flags:
                put(u, owner + flag, 1)
                desc = owner + slots[flag]
                u.mem_write(desc, b'\x01')
                u.mem_write(desc + 2, struct.pack('<2H', 5568 if allocate else width,
                                                 3712 if allocate else height))
                put(u, desc + 8, RAM + 0x40000 + slots[flag] * 16)
            events.clear()
            assert c.call(0x100A4D20, (job,)) == 0
            wrapper = next(e for e in events if e['kind'] == 'native_decode_wrapper')
            assert wrapper['arguments'] == [owner + selected, replacement + 0x18 if allocate else dest, 0]
            codec = next(e for e in events if e['kind'] == 'codec_replaced')
            assert codec['compressed'] == RAM + 0x40000 + selected * 16
            assert codec['planes'] == [y + (0x1000 if allocate else 0), chroma + (0x1000 if allocate else 0)]
            assert sum(e['kind'] == 'image_allocation_replaced' for e in events) == int(allocate)
            assert events[-1] == dict(kind='decode_wrapper_return', result=int(not failed))
            rows.append(dict(mode=mode, available_flags=flags, target_dimensions=[width, height],
                             selected_descriptor_offset=hex(selected), allocated_replacement=allocate,
                             codec_status_replaced=int(failed), stage_return=0, calls=list(events)))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/input-trace.json'))
    args = p.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Output must differ from source')
    data = extract_eg(args.source.read_bytes())
    StockImage(data)
    rows = selection(data)
    bindings = save_input_binding(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_sha256=sha256(data), cases=rows, save_input_binding=bindings,
        limitations=['Synthetic structures and codec results; no pixel decode or camera access.',
                     'Direct stage invocation; runtime stage-table initialization remains unresolved.',
                     'Stage return zero alone does not prove codec success.']), indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(rows)} source-selection and {len(bindings)} save-input binding cases; no real decode.')


if __name__ == '__main__':
    main()
