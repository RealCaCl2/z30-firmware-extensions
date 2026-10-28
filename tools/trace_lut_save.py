"""Trace stock retouch save-job selection, retained planes and JPEG assembly.

Bounded synthetic native execution only. Effect pixels, codecs, image hardware,
allocators and selected derived-image stages are replaced; no SD/camera output.
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
from trace_lut_retouch import word, put, returning, image_descriptor
from trace_lut_buffers import registers
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP

PREVIEW_TABLE, SAVE_TABLE = 0xC18872D0, 0xC1889684


def table_inventory(data):
    stock = StockImage(data)
    rows = []
    for table in (PREVIEW_TABLE, SAVE_TABLE):
        phases = []
        for lane in range(11):
            for phase, node in enumerate(stock.words(table + lane * 60, 15)):
                if not node:
                    continue
                descriptor = stock.words(node, 10)
                stages = []
                try:
                    words = stock.words(descriptor[5], 60)
                except ValueError:
                    # Runtime tables in BSS are deliberately not read as ROM.
                    words = None
                if words is not None:
                    for index in range(20):
                        flag, before, processor = words[index * 3:index * 3 + 3]
                        if any((flag, before, processor)):
                            stages.append(dict(index=index, flag=flag, before=hex(before), processor=hex(processor)))
                phases.append(dict(lane=lane, phase=phase, node=hex(node),
                                   stages_address=hex(descriptor[5]), initialized_stages=stages,
                                   runtime_stages=words is None))
        rows.append(dict(table=hex(table), phases=phases))
    return rows


def job_creation(data):
    c = Context(data, data)
    c.map(0xC23513A8, 0x80)
    u = c.u
    request, image, metadata, control, job, param, output = [RAM + x for x in
        (0x1000, 0x2000, 0x3000, 0x8000, 0x9000, 0xE000, 0xF000)]
    events = []

    def boundary(uc, address, size, unused):
        if address == 0x10211F4C:
            length = registers(uc, 1)[0]
            assert length in (0x2CC, 0x4740)
            events.append(dict(kind='allocate_replaced', length=length))
            returning(uc, control if length == 0x2CC else job)
        elif address in (0x10199BB0, 0x10199BAA):
            returning(uc)
        elif address == 0x1007FF16:
            assert registers(uc, 1)[0] == control
            events.append(dict(kind='scheduler_start_replaced', table=hex(word(uc, control + 4))))
            returning(uc, 1)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for phase in (0, 1):
        for operation in (1, 5, 6, 7):
            u.mem_write(request, bytes(0xA0))
            u.mem_write(image, bytes(0x400))
            u.mem_write(metadata, bytes(0x3200))
            u.mem_write(control, bytes(0x2CC))
            u.mem_write(job, bytes(0x4740))
            put(u, image + 0x3A4, metadata)
            u.mem_write(metadata + 0x2658, bytes([operation]))
            put(u, request, image)
            u.mem_write(request + 4, bytes([phase]))
            u.mem_write(request + 8, bytes([0 if operation == 1 else 6]))
            put(u, request + 0xC, param)
            put(u, request + 0x98, output)
            events.clear()
            assert c.call(0x102218D0, (request,)) == 1
            expected = PREVIEW_TABLE if phase == 0 else SAVE_TABLE
            assert word(u, control + 4) == expected
            assert word(u, control) == job and word(u, job + 0xC) == image
            assert u.mem_read(job + 0x4690, 1)[0] == phase
            assert u.mem_read(job + 0x4694, 1)[0] == (0 if operation == 1 else 6)
            assert word(u, job + 0x4698) == param and word(u, job + 0x4724) == output
            rows.append(dict(phase=phase, operation=operation, selected_table=hex(expected), calls=list(events)))
    return rows


def bounded_call(c, entry, args, stop):
    try:
        c.call(entry, args)
    except ValueError:
        if c.u.reg_read(UC_ARM_REG_PC) != stop:
            raise
    else:
        raise AssertionError('Expected an intentional stop in the original stage table')


def effect_to_main_encode(data):
    """Two adjacent original phases, with explicit stops before derived images."""
    c = Context(data, data)
    for address, size in ((0xC2351AD4, 0x800), (0xC235222C, 0x28), (0xC2359330, 0x180)):
        c.map(address, size)
    u = c.u
    job, image, metadata, source, param, obj, derived, owner, service, stage, codec = [RAM + x for x in
        (0x1000, 0x6000, 0x7000, 0xB000, 0xB100, 0xB200, 0xB300, 0xB400, 0xC000, 0xC400, 0xD000)]
    y_in, c_in, y_out, c_out, compressed = [RAM + x for x in (0x20000, 0x21000, 0x22000, 0x23000, 0x30000)]
    stock = StockImage(data)
    phase4 = stock.words(stock.words(SAVE_TABLE + 4 * 4, 1)[0] + 0x14, 1)[0]
    phase5 = stock.words(stock.words(SAVE_TABLE + 5 * 4, 1)[0] + 0x14, 1)[0]
    events, stopped_at, failed = [], 0, False

    def boundary(uc, address, size, unused):
        if address == stopped_at:
            events.append(dict(kind='intentional_stop', entry=hex(address)))
            uc.emu_stop()
        elif address == 0x10142ACA:
            assert registers(uc, 2)[1] == metadata
            events.append(dict(kind='image_allocation_replaced', object=hex(obj)))
            returning(uc, obj)
        elif address == 0x100CCAD4:
            returning(uc, 0)  # No synthetic workspace entries requested.
        elif address == 0x100CCA42:
            effect, p = registers(uc, 2)
            src, dst = word(uc, p + 4), word(uc, p + 0x10)
            assert src == source and dst == obj + 0x18
            assert [word(uc, dst + x) for x in (0x14, 0x18)] == [y_out, c_out]
            events.append(dict(kind='effect_task_replaced', effect=effect, input_descriptor=hex(src),
                               output_descriptor=hex(dst), output_planes=[hex(y_out), hex(c_out)]))
            # Sentinel only, not a LUT or a stock effect algorithm.
            uc.mem_write(y_out, b'\x5A' * 0x400)
            uc.mem_write(c_out, b'\xA5' * 0x400)
            put(uc, p + 0x14, 1)
            returning(uc, 1)
        elif address in (0x100E9CAE, 0x100E9B08):
            events.append(dict(kind='image_service_replaced', entry=hex(address), mode=registers(uc, 1)[0]))
            returning(uc)
        elif address in (0x10199ECA, 0x10199E0A):
            returning(uc, 1)
        elif address == 0x101D5AE0:
            returning(uc, 1)  # Synthetic JPEG output enabled in metadata.
        elif address == 0x10211F4C:
            assert registers(uc, 2) == [0xE0, 0]
            returning(uc, codec)
        elif address == 0x10219468:
            p = registers(uc, 1)[0]
            assert p == codec
            assert [word(uc, p + x) for x in (0x14, 0x18)] == [y_out, c_out]
            assert uc.mem_read(y_out, 16) == b'\x5A' * 16 and uc.mem_read(c_out, 16) == b'\xA5' * 16, (
                bytes(uc.mem_read(y_out, 16)).hex(), bytes(uc.mem_read(c_out, 16)).hex(), events)
            assert word(uc, image + 0x30C) == obj
            events.append(dict(kind='codec_replaced', format=uc.mem_read(p, 1)[0],
                               dimensions=list(struct.unpack('<2H', bytes(uc.mem_read(p + 2, 4)))),
                               input_planes=[hex(y_out), hex(c_out)], input_sentinels_present=True))
            uc.mem_write(p + 0x40, bytes([int(not failed)]))
            put(uc, p + 0x44, compressed)
            put(uc, p + 0x48, 1234)
            returning(uc, int(failed))
        elif address == 0x10211F5E:
            assert registers(uc, 1)[0] == codec
            events.append(dict(kind='codec_job_free_replaced'))
            returning(uc)
        elif address == 0x100A619C:
            events.append(dict(kind='encode_return', wrapper_return=registers(uc, 1)[0],
                               result_status=word(uc, uc.reg_read(UC_ARM_REG_SP) + 0x3C)))
        elif address == 0x10142B50:
            assert registers(uc, 1)[0] == obj
            events.append(dict(kind='image_release_replaced'))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for effect in (0, 6):
        for fmt in (1, 2):
            for failed in (False, True):
                for a, n in ((job, 0x4740), (image, 0x400), (metadata, 0x3200),
                             (source, 0xC00), (service, 0x300), (stage, 0x2C), (codec, 0xE0)):
                    u.mem_write(a, bytes(n))
                put(u, job + 8, service)
                put(u, job + 0xC, image)
                u.mem_write(job + 0x4690, b'\x01')
                u.mem_write(job + 0x4694, bytes([effect]))
                put(u, job + 0x4698, param)
                put(u, image + 0x3A4, metadata)
                put(u, image + 0x31C, derived)
                put(u, image + 0x320, owner)
                u.mem_write(metadata, b'\x01')
                u.mem_write(metadata + 0x2658, bytes([1 if effect == 0 else 5]))
                put(u, param + 4, source)
                put(u, param + 8, RAM + 0xB800)  # Non-null workspace descriptor, with zero entries.
                image_descriptor(u, source, fmt, 64, 32, 16, y_in, c_in)
                image_descriptor(u, obj + 0x18, fmt, 64, 32, 16, y_out, c_out)
                put(u, owner + 0x3C, 0x10000)
                put(u, owner + 0x48, compressed)
                put(u, stage, job)
                put(u, stage + 4, phase4)
                events.clear()
                stopped_at = 0x100A42C8  # Original phase 4, step 3: compressed-buffer preparation.
                bounded_call(c, 0x100CCDC4, (0xC189F2F8, stage), stopped_at)
                assert word(u, image + 0x30C) == obj
                assert sum(e['kind'] == 'effect_task_replaced' for e in events) == 1
                assert not any(e['kind'] == 'image_release_replaced' for e in events)
                assert [e['mode'] for e in events if e['kind'] == 'image_service_replaced'] == [1, 13]
                events.append(dict(kind='phase4_retains_main_buffer'))
                # Main compressed owner is supplied by fixture. Remaining phase-4 derived-image stages are skipped.
                put(u, stage + 4, phase5)
                put(u, stage + 0x24, 0)
                stopped_at = 0x100A68D4  # Phase 5 step 2, after main encode and its cleanup.
                bounded_call(c, 0x100CCDC4, (0xC189F334, stage), stopped_at)
                kinds = [e['kind'] for e in events]
                assert kinds.index('codec_replaced') < kinds.index('image_release_replaced')
                assert word(u, image + 0x30C) == 0
                assert word(u, owner + 0x4C) == 1234 and word(u, owner + 8) == 1
                result_status = next(e['result_status'] for e in events if e['kind'] == 'encode_return')
                assert result_status == int(failed)
                assert [word(u, 0xC2359330 + x) for x in (8, 12)] == [compressed, 1234]
                rows.append(dict(effect=effect, format=fmt, substituted_codec_error=failed,
                                 encoded_owner_flag=word(u, owner + 8), encoded_length=word(u, owner + 0x4C),
                                 result_status=result_status, phase4_returned=False, phase5_returned=False,
                                 calls=list(events)))
    return rows


def backend_completion(data):
    c = Context(data, data)
    c.map(0xC23513A8, 0x80)
    u = c.u
    control, job, image = RAM + 0x1000, RAM + 0x2000, RAM + 0x7000
    events = []

    def boundary(uc, address, size, unused):
        if address in (0x10199BB0, 0x10199BAA):
            returning(uc)
        elif address == 0x100A11A6:
            returning(uc, 0)
        elif address == 0x10199E04:
            events.append(dict(kind='event_replaced', arguments=registers(uc, 2)))
            returning(uc)
        elif address == 0x10211F5E:
            events.append(dict(kind='free_replaced', pointer=hex(registers(uc, 1)[0])))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for job_error in (0, 1):
        for effect_submit_return in (0, 1):
            u.mem_write(control, bytes(0x2CC))
            u.mem_write(job, bytes(0x4740))
            put(u, control, job)
            put(u, job + 4, job_error)
            put(u, job + 0xC, image)
            put(u, job + 0x472C, effect_submit_return)
            put(u, 0xC23513B4, 0x1034001D)
            events.clear()
            c.call(0x1021ECD4, (0x43080001, control))
            assert u.mem_read(image + 4, 1)[0] == job_error
            expected_mask = 0x100 if not job_error and effect_submit_return == 1 else 0x200
            assert events == [dict(kind='event_replaced', arguments=[0xC295C6B4, expected_mask]),
                              dict(kind='free_replaced', pointer=hex(job)),
                              dict(kind='free_replaced', pointer=hex(control))]
            assert word(u, 0xC23513B4) == 0
            rows.append(dict(job_error=job_error, effect_submit_return=effect_submit_return, calls=list(events)))
    return rows


def jpeg_assembly(data):
    c = Context(data, data)
    u = c.u
    handle, metadata, owner, workspace, node, request = [RAM + x for x in
        (0x1000, 0x2000, 0x6000, 0x6200, 0x6300, 0x7000)]
    header, compressed = RAM + 0x20000, RAM + 0xA0000
    events, emitted, writes, fail_write = [], bytearray(), [], False

    def boundary(uc, address, size, unused):
        if address == 0x10338686:
            returning(uc, 0)  # Synthetic JPEG file kind.
        elif address == 0x102B33BE:
            p = registers(uc, 1)[0]
            events.append(dict(kind='metadata_serialization_replaced', request=hex(p)))
            put(uc, word(uc, p + 0x10) + 8, 0x120)
            returning(uc, 1)
        elif address == 0x102C8DC6:
            fd, pointer, length, actual = registers(uc)
            assert fd == 99
            writes.append(dict(pointer=hex(pointer), length=length))
            put(uc, actual, 0 if fail_write else length)
            if not fail_write:
                emitted.extend(uc.mem_read(pointer, length))
            returning(uc, 0xA0000002 if fail_write else 0)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for length in (1234, 0x10000):
        u.mem_write(metadata, bytes(0x3200))
        u.mem_write(metadata + 0x2658, b'\x05')
        u.mem_write(owner, bytes(0x124))
        u.mem_write(request, bytes(0x90))
        header_bytes = b'H' * 0x120  # Deliberately not serialized EXIF/JPEG metadata.
        compressed_bytes = b'\xFF\xD8' + bytes(i % 251 for i in range(length - 2))
        u.mem_write(header, header_bytes)
        u.mem_write(compressed, compressed_bytes)
        put(u, owner + 0x48, compressed)
        put(u, owner + 0x4C, length)
        put(u, workspace + 4, node)
        put(u, node, header)
        put(u, STACK + 0xF0000, request)
        assert c.call(0x100A6A92, (handle, metadata, owner, workspace)) == 0
        assert word(u, request) == owner + 0x40
        assert word(u, request + 0x3C) == request and word(u, request + 0x40) == request + 0x68
        events.clear()
        assert c.call(0x1033DF24, (0, 0, request + 0x30)) == 1
        assert word(u, request + 0x5C) == length - 2
        total = word(u, request + 0x48)
        assert total == 0x120 + length - 2 and word(u, request + 0x4C) == 0
        segments = []
        for kind in (0, 3, 1, 4, 2, 5):
            status = c.call(0x102B688E, (0, request + 0x30, kind, RAM + 0x8000))
            descriptor = list(struct.unpack('<3I', bytes(u.mem_read(RAM + 0x8000, 12))))
            if kind == 0:
                assert status == 0 and descriptor == [header, 0x120, 0]
            elif kind == 3:
                assert status == 0 and descriptor == [compressed + 2, length - 2, 0]
            else:
                assert status == 1
            segments.append(dict(kind=kind, status=status, descriptor=descriptor))
        sink_cases = []
        for fail_write in (False, True):
            emitted.clear()
            writes.clear()
            status = c.call(0x102B6EC0, (99, 0, request + 0x30))
            assert status == (2 if fail_write else 0)
            assert bytes(emitted) == (b'' if fail_write else header_bytes + compressed_bytes[2:])
            assert len(writes) == (1 if fail_write else 2)
            sink_cases.append(dict(file_boundary_error=fail_write, returned=status, bytes=len(emitted), calls=list(writes)))
        rows.append(dict(compressed_length=length, header_length_substituted=0x120,
                         compressed_skip=2, payload_length=word(u, request + 0x5C),
                         assembly_total_length=total, segments=segments, sink_cases=sink_cases, calls=list(events)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/save-trace.json'))
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Output must differ from source')
    data = extract_eg(args.source.read_bytes())
    StockImage(data)
    groups = dict(job_creation=job_creation(data), effect_to_main_encode=effect_to_main_encode(data),
                  backend_completion=backend_completion(data), jpeg_assembly=jpeg_assembly(data))
    report = dict(source_sha256=sha256(data), stage_tables=table_inventory(data), **groups, camera_access=False,
                  limitations=[
                      'No real scheduler, original JPEG decode, LUT, hardware cache, DMA, SD or camera.',
                      'Original phase tables/interpreter run with explicit stops before derived-image work.',
                      'Phase 4 remainder and phase 5 derived-image encoding are skipped, not validated.',
                      'Effect output is a synthetic sentinel; codec status/address/length are substituted.',
                      'JPEG metadata serialization is replaced; no valid JPEG is emitted.',
                      'Native writer bytes are captured in memory at the file-service boundary; no file is created.',
                      'Adjacent phase order is in the stock table; phase advancement is driven by fixture.',
                      'Backend completion and container assembly are separate fixtures, not a full save.',
                  ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS:', ', '.join(f'{len(rows)} {name}' for name, rows in groups.items()))
    print('Total:', sum(map(len, groups.values())), 'bounded cases; no real JPEG or SD output.')


if __name__ == '__main__':
    main()
