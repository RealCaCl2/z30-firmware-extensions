"""Trace stock retouch requests into effect descriptors, stopping before hardware.

Synthetic message/geometry inputs only. No camera access, JPEG processing,
firmware patch, real queue, DMA, cache maintenance or filesystem implementation.
Human-facing effect names remain an inference until UI state is bound.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM, STACK
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR, UC_ARM_REG_PC

REQUEST = 0xC2957560
BACKEND = 0xC295BD18
UI, OPTIONS, RESULT, META, IMAGE, WORK = [RAM + x for x in
                                       (0x1000, 0x2000, 0x3000, 0x4000, 0x8000, 0x9000)]


def word(u, address):
    return struct.unpack('<I', bytes(u.mem_read(address, 4)))[0]


def put(u, address, value):
    u.mem_write(address, struct.pack('<I', value))


def returning(u, value=0):
    u.reg_write(UC_ARM_REG_R0, value)
    u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))


def constructors(data):
    context = Context(data, data)
    context.map(REQUEST, 0x200)
    u = context.u
    captured = []

    def boundary(uc, address, size, unused):
        if address in (0x10426F2A, 0x10426F4E):
            returning(uc)
        elif address == 0x10348130:
            message = uc.reg_read(UC_ARM_REG_R0)
            pointer = word(uc, message + 0x14)
            parameters = word(uc, pointer + 0x2C)
            captured.append(dict(message=hex(word(uc, message + 0x10)),
                                 phase=uc.mem_read(pointer + 8, 1)[0],
                                 operation=uc.mem_read(pointer + 9, 1)[0],
                                 plane_addresses=[hex(word(uc, pointer + x)) for x in (0x10, 0x14)],
                                 dimensions=list(struct.unpack('<2H', bytes(uc.mem_read(pointer + 0x1E, 4)))),
                                 parameters=list(uc.mem_read(parameters, 2))))
            put(uc, pointer + 4, 0x01234567)  # Synthetic result handle, no DCF creation.
            uc.mem_write(pointer + 0xC, b'\0')
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for phase, entry in ((0, 0x104235A2), (1, 0x10424672)):
        for mode in range(3):
            for strength in range(3):
                u.mem_write(REQUEST, bytes(0x200))
                u.mem_write(UI, bytes(0x70))
                u.mem_write(UI + 9, bytes([phase]))
                put(u, UI + 0x14, RAM + 0x20000)
                u.mem_write(UI + 0x18, struct.pack('<2H', 32, 16))
                u.mem_write(OPTIONS, bytes([mode, strength]))
                captured.clear()
                result = context.call(entry, (UI, OPTIONS, RESULT))
                assert result == 1 and len(captured) == 1
                row = captured[0]
                assert row['message'] == '0x4160004'
                assert row['phase'] == phase and row['operation'] == mode + 5
                assert row['parameters'][0] == strength
                if phase == 0:
                    assert row['plane_addresses'] == [hex(RAM + 0x20000), hex(RAM + 0x20200)]
                    assert row['dimensions'] == [32, 16]
                else:
                    assert word(u, RESULT) == 0x01234567 and u.mem_read(RESULT + 4, 1)[0] == 0
                rows.append(dict(entry=hex(entry), mode=mode, strength=strength, **row))
    return rows


def dispatch_routes(data):
    context = Context(data, data)
    context.map(0xC295C704, 4)
    u = context.u
    message, parameter, callback = RAM + 0xA000, RAM + 0xA100, RAM + 0xA200
    hits = []

    def boundary(uc, address, size, unused):
        if address in (0x10344828, 0x10342FE4, callback):
            hits.append(dict(address=hex(address),
                             r0=hex(uc.reg_read(UC_ARM_REG_R0)),
                             r1=hex(uc.reg_read(UC_ARM_REG_R1))))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for code, expected in ((0x04160004, 0x10344828), (0x0416000C, 0x10342FE4)):
        u.mem_write(message, bytes(0x1C))
        u.mem_write(message + 0xC, struct.pack('<4I', callback | 1, code, parameter, 0x1234))
        hits.clear()
        context.call(0x10347B04, (message,))
        assert len(hits) == 2 and hits[0]['address'] == hex(expected)
        assert hits[0]['r0'] == hex(parameter)
        assert hits[1] == dict(address=hex(callback), r0=hex(code), r1='0x1234')
        rows.append(dict(code=hex(code), calls=list(hits)))
    return rows


def effect_setup(data):
    context = Context(data, data)
    context.map(BACKEND, 0x400)
    u = context.u
    captured = []

    def boundary(uc, address, size, unused):
        if address == 0x1038F50E:
            returning(uc, 1)  # Do not create or reserve destination files.
        elif address == 0x1038F406:
            p = word(uc, BACKEND + 0xC)
            captured.append(dict(effect_index=uc.mem_read(BACKEND + 8, 1)[0],
                                 source_descriptor=hex(word(uc, p + 4)),
                                 parameter_address=hex(p),
                                 mode=uc.mem_read(p + 0xC, 1)[0],
                                 strength=uc.mem_read(p + 0xD, 1)[0]))
            returning(uc)  # Do not start the asynchronous backend.

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for operation in (1, 5, 6, 7):
        for strength in range(3):
            u.mem_write(UI, bytes(0x400))
            u.mem_write(META, bytes(0x3000))
            put(u, UI + 0x3A4, META)
            u.mem_write(OPTIONS, bytes([strength, 0, 0, 0]))
            put(u, STACK + 0xF0000, OPTIONS)  # Fifth AAPCS argument.
            captured.clear()
            assert context.call(0x1038F64A, (UI, IMAGE, 0, operation)) == 0
            assert len(captured) == 1
            row = captured[0]
            assert row['source_descriptor'] == hex(IMAGE)
            assert row['effect_index'] == (0 if operation == 1 else 6)
            assert row['mode'] == (strength if operation == 1 else operation - 5)
            if operation != 1:
                assert row['strength'] == strength
            rows.append(dict(operation=operation, input_strength=strength, **row))
    return rows


def image_descriptor(u, address, format_code, stride, width, height, y, c):
    u.mem_write(address, bytes(0x1C))
    u.mem_write(address, bytes([format_code]))
    u.mem_write(address + 2, struct.pack('<6H', stride, stride, width, height, width, height))
    put(u, address + 0x14, y)
    put(u, address + 0x18, c)


def hardware_boundary(data):
    context = Context(data, data)
    context.map(0xC1EEA000, 0x2000)
    u = context.u
    source, output = IMAGE, IMAGE + 0x40
    parameter = RAM + 0xB000
    pixels = RAM + 0x20000
    reached, replaced = [], []

    def boundary(uc, address, size, unused):
        if address == 0x10199E0A:
            replaced.append(hex(address))
            returning(uc)
        elif address == 0x1020182C:
            state = 0xC1EEA4C0
            reached.append(dict(submit_descriptor=hex(uc.reg_read(UC_ARM_REG_R0)),
                                plane_addresses=[hex(word(uc, state + x)) for x in (0, 4, 8, 12)],
                                table_addresses=[hex(word(uc, state + x)) for x in (0x14, 0x1C, 0x24, 0x2C)]))
            uc.emu_stop()

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    sentinel = bytes((i * 37 + 13) & 255 for i in range(0x4000))
    for format_code in (1, 2):
        for mode in range(3):
            u.mem_write(pixels, sentinel)
            image_descriptor(u, source, format_code, 64, 32, 16, pixels, pixels + 0x800)
            image_descriptor(u, output, format_code, 64, 32, 16, pixels + 0x1000, pixels + 0x1800)
            put(u, WORK, 1)
            put(u, WORK + 4, RAM + 0x40000)
            u.mem_write(parameter, struct.pack('<IIIBB2xII', 0, source, WORK, mode, 0, output, 0))
            reached.clear()
            replaced.clear()
            try:
                context.call(0x1025FB8E, (parameter,))
            except ValueError:
                # An intentional stop before hardware submission is not a function return.
                if u.reg_read(UC_ARM_REG_PC) != 0x1020182C or len(reached) != 1:
                    raise
            else:
                raise AssertionError('Expected to stop before hardware')
            assert reached[0]['plane_addresses'] == [hex(pixels + x) for x in (0, 0x800, 0x1000, 0x1800)]
            assert bytes(u.mem_read(pixels, len(sentinel))) == sentinel
            rows.append(dict(format_code=format_code, mode=mode, reached=reached[0],
                             event_clear_replaced=list(replaced), pixels_unchanged=True,
                             returned=False, full_frame_test=False))
    return rows


def addressing(data):
    context = Context(data, data)
    rows = []
    for format_code, expected_y in ((1, 4), (2, 2)):
        put(context.u, STACK + 0xF0000, format_code)
        result = context.call(0x1025FBCA, (64, 16, 4, RAM + 0x20000))
        assert result == RAM + 0x20000 + 16 + 64 * expected_y
        rows.append(dict(format_code=format_code, stride=64, x=16, y=4, address=hex(result)))
    return rows


def bind_output(data):
    context = Context(data, data)
    u = context.u
    request, parameter, source, output, scratch = [RAM + x for x in
                                                 (0xA000, 0xA100, 0xA200, 0xA240, 0xA300)]
    rows = []
    for effect in (0, 6):
        for format_code in (1, 2):
            u.mem_write(request, struct.pack('<II', effect, parameter))
            u.mem_write(parameter, bytes(0x18))
            put(u, parameter + 4, source)
            image_descriptor(u, source, format_code, 64, 32, 16, RAM + 0x10000, RAM + 0x10800)
            image_descriptor(u, output, 1, 16, 8, 8, RAM + 0x20000, RAM + 0x20800)
            assert context.call(0x100A535A, (request, output, scratch, META)) == 1
            assert word(u, parameter + 0x10) == output
            assert bytes(u.mem_read(output, 0x14)) == bytes(u.mem_read(source, 0x14))
            assert [word(u, output + x) for x in (0x14, 0x18)] == [RAM + 0x20000, RAM + 0x20800]
            rows.append(dict(effect_index=effect, format_code=format_code,
                             output_bound=True, geometry_copied=True, destination_planes_preserved=True))
    return rows


def worker_completion(data):
    context = Context(data, data)
    u = context.u
    message, parameter, callback = RAM + 0xA000, RAM + 0xA100, RAM + 0xA200
    processors = {0: 0x102532EC, 6: 0x1025FB8E}
    hits = []

    def boundary(uc, address, size, unused):
        if address in processors.values():
            p = uc.reg_read(UC_ARM_REG_R0)
            assert p == parameter
            put(uc, p + 0x14, 1)  # Simulated effect status; no pixel implementation.
            hits.append(dict(kind='processor_replaced', address=hex(address)))
            returning(uc, 1)
        elif address == callback:
            hits.append(dict(kind='caller_completion', label=hex(uc.reg_read(UC_ARM_REG_R0)),
                             parameter=hex(uc.reg_read(UC_ARM_REG_R1)), status=word(uc, parameter + 0x14)))
            returning(uc)
        elif address in (0x10211F5E, 0x10199E04):
            hits.append(dict(kind='recycle' if address == 0x10211F5E else 'event_replaced'))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for effect in processors:
        for with_callback in (False, True):
            u.mem_write(message, bytes(0x1C))
            u.mem_write(parameter, bytes(0x18))
            put(u, parameter, callback | 1 if with_callback else 0)
            u.mem_write(message + 0xC, struct.pack('<4I', 0x100CCA13, effect, parameter, message))
            hits.clear()
            context.call(0x100CC910, (message,))
            assert [h['kind'] for h in hits] == ['processor_replaced',
                    'caller_completion' if with_callback else 'event_replaced', 'recycle']
            if with_callback:
                assert hits[1]['status'] == 1 and hits[1]['parameter'] == hex(parameter)
                assert hits[1]['label'] == hex(0x43100002 + effect)
            rows.append(dict(effect_index=effect, with_callback=with_callback, calls=list(hits)))
    return rows


def trace(data):
    StockImage(data)
    return dict(source_sha256=sha256(data), camera_access=False,
                request_constructors=constructors(data),
                message_routes=dispatch_routes(data),
                operation_to_effect=effect_setup(data),
                chroma_addressing=addressing(data),
                output_binding=bind_output(data),
                worker_and_completion=worker_completion(data),
                stopped_before_hardware=hardware_boundary(data),
                limitations=[
                    'No real JPEG, sensor image, camera, task scheduling, DMA or file operation.',
                    'UI event waits, message submission, destination preparation and backend start are stubbed.',
                    'Effect setup is connected across separate fixtures, not one complete stock execution.',
                    'Monochrome and D-Lighting family names are inferred from option/algorithm behavior; no UI trace.',
                    'Hardware cases execute descriptor construction then stop; no effect pixels are produced.',
                    'Format 1/2 chroma subsampling is recovered; byte order, matrix, range and live ownership remain unknown.',
                ])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/retouch-trace.json'))
    args = p.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Output must differ from source')
    report = trace(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS: 18 constructors, 2 routes, 12 effect setups, 2 address cases, 4 bindings, 4 worker/completion cases, 6 hardware-boundary stops')
    print('No JPEG processing or camera validation. Hardware never submitted.')


if __name__ == '__main__':
    main()
