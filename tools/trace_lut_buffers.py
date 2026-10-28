"""Execute bounded stock Z30 retouch buffer/cache/codec contracts offline.

Queue, event, allocator and codec-driver boundaries are explicitly substituted.
No cache model, DMA, image processing, camera access or JPEG/file output.
"""
import argparse
import io
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM
from trace_lut_retouch import word, put, returning, image_descriptor
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC


def registers(u, count=4):
    return [u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[:count]]


def cache_ranges(data):
    c = Context(data, data)
    u = c.u
    policy, events = 1, []
    mcr = {0x10204B2E: 'clean', 0x10204B6A: 'invalidate', 0x10204B9E: 'clean_invalidate'}

    def boundary(uc, address, size, unused):
        if address == 0x102185C2:
            returning(uc, policy)  # Actual region policy is not seeded.
        elif address in mcr:
            events.append(dict(kind=mcr[address], line=hex(uc.reg_read(UC_ARM_REG_R2))))
            uc.reg_write(UC_ARM_REG_PC, (address + size) | 1)  # No hardware cache emulation.
        elif address == 0x10035AD0:
            events.append(dict(kind='svc_wrapper_replaced', arguments=registers(uc, 2)))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for entry, kind in ((0x10204B0C, 'clean'), (0x10204B48, 'invalidate'), (0x10204B7C, 'clean_invalidate')):
        for offset, length, policy in ((0, 0, 1), (3, 0, 1), (3, 130, 1),
                                       (0, 64, 1), (0, 0x45000, 1),
                                       (0, 0x45001, 1), (3, 130, 0)):
            pointer = RAM + 0x20000 + offset
            events.clear()
            assert c.call(entry, (pointer, length)) == 0
            if not policy:
                assert not events
            elif length > 0x45000:
                assert events == [dict(kind='svc_wrapper_replaced', arguments=[3, 2])]
            else:
                assert events == [dict(kind=kind, line=hex(a)) for a in
                                  range(pointer & ~63, pointer + length, 64)]
            rows.append(dict(entry=hex(entry), pointer=hex(pointer), length=length,
                             synthetic_region_policy=policy, line_count=sum('line' in e for e in events),
                             events=events[:4], events_truncated=len(events) > 4))
    return rows


def allocations(data):
    c = Context(data, data)
    u = c.u
    obj, node, metadata, owner, payload = [RAM + x for x in (0x1000, 0x2000, 0x3000, 0x4000, 0x20000)]
    put(u, obj + 4, node)
    put(u, node, payload)
    put(u, node + 4, 0x600)
    events, available, allocated = [], 1, obj

    def boundary(uc, address, size, unused):
        if address == 0x10141C94:
            events.append(dict(kind='pool_available_replaced', pool=registers(uc, 1)[0]))
            returning(uc, available)
        elif address == 0x100CC512:
            events.append(dict(kind='allocate_replaced', arguments=registers(uc, 3)))
            returning(uc, allocated)
        elif address == 0x1014237A:
            events.append(dict(kind='descriptor_init_replaced', arguments=registers(uc, 3)))
            returning(uc)
        elif address == 0x10204B7C:
            events.append(dict(kind='cache_replaced', arguments=registers(uc, 2)))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for available, fallback, allocated in ((1, 0, obj), (1, 0, 0), (0, 0, obj), (0, 1, obj), (0, 1, 0)):
        u.mem_write(metadata + 0xED, bytes([fallback]))
        events.clear()
        result = c.call(0x10142ACA, (owner, metadata))
        expect_obj = bool(allocated and (available or fallback))
        assert result == (obj if expect_obj else 0)
        assert sum(e['kind'] == 'cache_replaced' for e in events) == int(bool(available and allocated))
        if expect_obj:
            assert events[1]['arguments'] == [owner, 9 if available else 16, 0 if available else 0x276C000]
            assert events[2]['arguments'] == [obj, metadata, 0]
            if available:
                assert events[3]['arguments'] == [payload, 0x600]
        rows.append(dict(pool9_available=available, fallback_enabled=fallback,
                         allocator_returns_object=bool(allocated), result=hex(result), calls=list(events)))
    return rows


def post_effect_lifetime(data):
    c = Context(data, data)
    c.map(0xC2351AD4, 0x800)
    c.map(0xC235222C, 0x28)
    u = c.u
    job, image, obj, target = [RAM + x for x in (0x1000, 0x6000, 0x7000, 0x8000)]
    events, timeout = [], False

    def boundary(uc, address, size, unused):
        if address in (0x100E9CAE, 0x100E9B08):
            events.append(dict(kind='image_service_replaced', entry=hex(address), arguments=registers(uc, 3)))
            assert word(uc, 0xC235222C) == obj + 0x18
            assert word(uc, 0xC2352234) == target
            returning(uc)
        elif address == 0x10199ECA:
            events.append(dict(kind='wait_replaced', arguments=registers(uc)))
            returning(uc, 0 if timeout else 1)
        elif address == 0x10199E0A:
            events.append(dict(kind='event_clear_replaced', arguments=registers(uc, 2)))
            returning(uc)
        elif address == 0x10142B50:
            events.append(dict(kind='release_replaced', object=hex(registers(uc, 1)[0])))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for job_type in (0, 2):
        for timeout in (False, True):
            u.mem_write(job, bytes(0x4800))
            u.mem_write(image, bytes(0x400))
            put(u, job + 0xC, image)
            u.mem_write(job + 0x4690, bytes([job_type]))
            put(u, job + 0x4724, target)
            put(u, image + 0x30C, obj)
            events.clear()
            assert c.call(0x100A5BD2, (job,)) == 0
            assert [e['kind'] for e in events] == ['image_service_replaced', 'wait_replaced',
                   'event_clear_replaced', 'image_service_replaced', 'wait_replaced',
                   'event_clear_replaced', 'release_replaced']
            assert events[0]['arguments'] == [1, 0xC2351AD4, 0x100A0E87]
            assert events[3]['arguments'] == [13, 0xC2351AD4, 0x100A0E87]
            assert events[1]['arguments'] == [0xC23592DC, 0x10, 0, 3000]
            assert events[4]['arguments'] == [0xC23592DC, 0x20, 0, 3000]
            assert events[-1]['object'] == hex(obj) and word(u, image + 0x30C) == 0
            rows.append(dict(job_type=job_type, substituted_wait_return=0 if timeout else 1, calls=list(events)))
    return rows


def codec_routes(data):
    c = Context(data, data)
    u = c.u
    msg, param, callback = [RAM + x for x in (0x1000, 0x2000, 0x3000)]
    events, result = [], 0

    def boundary(uc, address, size, unused):
        if address in (0x1021923E, 0x10219634):
            events.append(dict(kind='codec_processing_replaced', entry=hex(address), arguments=registers(uc, 2)))
            returning(uc, result)
        elif address == callback:
            events.append(dict(kind='callback_replaced', arguments=registers(uc, 2)))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for code in (0x42181801, 0x42181802, 0x42181803):
        for result in (0, 1):
            u.mem_write(msg + 0xC, struct.pack('<4I', callback | 1, code, param, 0xABCD))
            events.clear()
            c.call(0x1021934C, (msg,))
            assert len(events) == 2
            assert events[0]['entry'] == hex(0x10219634 if code == 0x42181802 else 0x1021923E)
            if code != 0x42181802:
                assert events[0]['arguments'] == [msg, int(code == 0x42181803)]
            expected = (0x42181802 if code == 0x42181802 else 0x42181801) if result else 0x42181803
            assert events[1]['arguments'] == [expected, 0xABCD]
            rows.append(dict(request=hex(code), substituted_processing_return=result, calls=list(events)))
    return rows


def decode_wrapper(data):
    c = Context(data, data)
    u = c.u
    source, dest, compressed, y, chroma = [RAM + x for x in (0x1000, 0x2000, 0x10000, 0x20000, 0x30000)]
    events, status = [], 0

    def boundary(uc, address, size, unused):
        if address == 0x102194BC:
            p, owner = registers(uc, 2)
            events.append(dict(kind='synchronous_decode_replaced', owner=hex(owner),
                               compressed=hex(word(uc, p + 0x10)),
                               strides=[word(uc, p + x) for x in (0x18, 0x1C)],
                               planes=[hex(word(uc, p + x)) for x in (0x24, 0x28)],
                               format=uc.mem_read(p + 0x20, 1)[0]))
            uc.mem_write(p + 0x2C, bytes([int(status == 2)]))
            uc.mem_write(p + 0x2E, struct.pack('<2H', 32, 16))
            returning(uc, int(status == 1))

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for fmt in (1, 2):
        for status in (0, 1, 2):
            u.mem_write(source, bytes(0x20))
            u.mem_write(source, bytes([fmt]))
            put(u, source + 8, compressed)
            image_descriptor(u, dest, fmt, 64, 32, 16, y, chroma)
            events.clear()
            result = c.call(0x10096F88, (source, dest, 0))
            assert result == int(status == 0) and len(events) == 1
            assert events[0] == dict(kind='synchronous_decode_replaced', owner='0xffffffff',
                                    compressed=hex(compressed), strides=[64, 64],
                                    planes=[hex(y), hex(chroma)], format=fmt)
            assert struct.unpack('<2H', bytes(u.mem_read(dest + 0xA, 4))) == (32, 16)
            rows.append(dict(format=fmt, substituted_failure=status, wrapper_return=result, calls=list(events)))
    return rows


def encode_wrapper(data):
    c = Context(data, data)
    c.map(0xC295C230, 0xB8)
    u = c.u
    p, y, chroma, compressed = [RAM + x for x in (0x1000, 0x10000, 0x20000, 0x30000)]
    events, failed = [], False

    def boundary(uc, address, size, unused):
        if address in (0x103400E0, 0x103400D0):
            events.append(dict(kind='event_clear_replaced' if address == 0x103400E0 else 'wait_replaced',
                               mask=hex(registers(uc, 1)[0])))
            returning(uc, 0x80 if address == 0x103400D0 and failed else 0)
        elif address == 0x102193D2:
            message = registers(uc, 1)[0]
            config = word(uc, message + 0x14)
            events.append(dict(kind='codec_queue_replaced', message=hex(word(uc, message + 0x10)),
                               callback=hex(word(uc, message + 0xC)), format=uc.mem_read(config, 1)[0],
                               dimensions=list(struct.unpack('<2H', bytes(uc.mem_read(config + 2, 4)))),
                               strides=[word(uc, config + x) for x in (0xC, 0x10)],
                               input_planes=[hex(word(uc, config + x)) for x in (0x14, 0x18)],
                               compressed_destination=hex(word(uc, config + 0x1C))))
            put(uc, config + 0x48, 1234)  # Synthetic codec result; no bytes encoded.
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for width, height in ((32, 16), (5568, 3712)):
        for failed in (False, True):
            u.mem_write(p, bytes(0x28))
            put(u, p, y)
            put(u, p + 4, chroma)
            u.mem_write(p + 8, struct.pack('<2H', width, height))
            u.mem_write(p + 0x14, b'\x09')
            put(u, p + 0x18, compressed)
            put(u, p + 0x20, 0xDEADBEEF)
            u.mem_write(p + 0x24, struct.pack('<H2B', 4, 2, 0))
            events.clear()
            assert c.call(0x10390C42, (p,)) == int(not failed)
            assert [e['kind'] for e in events] == ['event_clear_replaced', 'codec_queue_replaced', 'wait_replaced']
            row = events[1]
            assert row['message'] == '0x42181801' and row['callback'] == '0x1034001d'
            assert row['format'] == 1 and row['dimensions'] == [width, height]
            assert row['strides'] == [(width + 15) & ~15] * 2
            assert row['input_planes'] == [hex(y), hex(chroma)]
            assert row['compressed_destination'] == hex(compressed)
            assert word(u, p + 0x20) == (0xDEADBEEF if failed else 1234)
            rows.append(dict(width=width, height=height, substituted_wait_error=failed,
                             output_length=word(u, p + 0x20), calls=list(events)))
    return rows


def jpeg_dimensions(data):
    """Run the stock SOF marker scanner on host-generated JPEG headers only."""
    from PIL import Image
    c = Context(data, data)
    u = c.u
    p, compressed = RAM + 0x1000, RAM + 0x10000
    rows = []
    for progressive in (False, True):
        stream = io.BytesIO()
        Image.new('RGB', (37, 19), (30, 90, 180)).save(stream, format='JPEG', progressive=progressive)
        blob = stream.getvalue()
        u.mem_write(compressed, blob)
        u.mem_write(p, bytes(12))
        put(u, p, compressed)
        put(u, p + 4, len(blob))
        assert c.call(0x102194E4, (p,)) == 0
        dimensions = list(struct.unpack('<2H', bytes(u.mem_read(p + 8, 4))))
        assert dimensions == [37, 19]
        rows.append(dict(progressive=progressive, bytes=len(blob), dimensions=dimensions,
                         scope='SOF marker scan only; no entropy or pixel decode'))
    return rows


def descriptor_to_encode_completion(data):
    """Native image-descriptor binder and result copier around a replaced codec."""
    c = Context(data, data)
    u = c.u
    p, owner, desc, allocation, result = [RAM + x for x in (0x1000, 0x2000, 0x3000, 0x4000, 0x5000)]
    y, chroma, compressed, notification = [RAM + x for x in (0x10000, 0x20000, 0x30000, 0x6000)]
    events, async_mode, failed = [], False, False

    def completed_fields(uc):
        uc.mem_write(allocation + 0x40, bytes([int(not failed)]))
        put(uc, allocation + 0x44, compressed)
        put(uc, allocation + 0x48, 1234)

    def boundary(uc, address, size, unused):
        if address == 0x10211F4C:
            assert registers(uc, 2) == [0xE0, 0]
            events.append(dict(kind='allocate_replaced', bytes=0xE0))
            returning(uc, allocation)
        elif address in (0x10219468, 0x102193D2):
            config = registers(uc, 1)[0]
            if address == 0x102193D2:
                assert word(uc, config + 0x10) == 0x42181801
                assert word(uc, config + 0xC) == 0x10096E97
                config = word(uc, config + 0x14)
            assert config == allocation
            events.append(dict(kind='codec_replaced', asynchronous=address == 0x102193D2,
                               format=uc.mem_read(config, 1)[0],
                               dimensions=list(struct.unpack('<2H', bytes(uc.mem_read(config + 2, 4)))),
                               strides=[word(uc, config + x) for x in (0xC, 0x10)],
                               planes=[hex(word(uc, config + x)) for x in (0x14, 0x18)],
                               destination=hex(word(uc, config + 0x1C)),
                               capacity=word(uc, config + 0x20)))
            if not async_mode:
                completed_fields(uc)
            returning(uc, int(failed))
        elif address == 0x100CCBA6:
            assert registers(uc, 1)[0] == notification
            events.append(dict(kind='notify_replaced'))
            returning(uc)
        elif address == 0x10211F5E:
            assert registers(uc, 1)[0] == allocation
            events.append(dict(kind='free_replaced'))
            returning(uc)

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for fmt in (1, 2):
        for async_mode in (False, True):
            for failed in (False, True):
                u.mem_write(p, bytes(0x30))
                u.mem_write(owner, bytes(0x20))
                u.mem_write(result, b'\xAA' * 0x60)
                u.mem_write(allocation, bytes(0xE0))
                image_descriptor(u, desc, fmt, 64, 32, 16, y, chroma)
                put(u, p, desc)
                put(u, p + 4, owner)
                put(u, p + 8, 0x10000)
                put(u, p + 0x28, notification if async_mode else 0)
                put(u, p + 0x2C, result)
                put(u, owner + 8, compressed)
                events.clear()
                returned = c.call(0x10096EE6, (p,))
                if async_mode:
                    assert returned == 1 and len(events) == 2
                    assert word(u, result) == 0xAAAAAAAA and word(u, owner + 0xC) == 0
                    completed_fields(u)
                    c.call(0x10096E96, (0x42181803 if failed else 0x42181801, allocation))
                else:
                    assert returned == int(not failed)
                assert [e['kind'] for e in events] == ['allocate_replaced', 'codec_replaced'] + (
                    ['notify_replaced'] if async_mode else []) + ['free_replaced']
                codec = events[1]
                assert codec['format'] == fmt and codec['dimensions'] == [32, 16]
                assert codec['strides'] == [64, 64] and codec['planes'] == [hex(y), hex(chroma)]
                assert codec['destination'] == hex(compressed) and codec['capacity'] == 0x10000
                assert word(u, result) == int(failed)
                assert [word(u, result + x) for x in (4, 8)] == [compressed, 1234]
                assert word(u, owner + 0xC) == 1234
                rows.append(dict(format=fmt, asynchronous=async_mode, substituted_codec_error=failed,
                                 wrapper_return=returned, result_status=word(u, result), calls=list(events)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/buffer-trace.json'))
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Output must differ from source')
    data = extract_eg(args.source.read_bytes())
    StockImage(data)
    groups = dict(cache_ranges=cache_ranges(data), allocations=allocations(data),
                  post_effect_lifetime=post_effect_lifetime(data), codec_routes=codec_routes(data),
                  decode_wrapper=decode_wrapper(data), encode_wrapper=encode_wrapper(data),
                  jpeg_dimensions=jpeg_dimensions(data),
                  descriptor_to_encode_completion=descriptor_to_encode_completion(data))
    report = dict(source_sha256=sha256(data), camera_access=False, **groups, limitations=[
        'Segmented synthetic fixtures, not an end-to-end retouch/save execution.',
        'CP15 operations intercepted; actual address-region policy and privileged SVC body not modeled.',
        'Queue, event, allocator, descriptor initializer and codec-processing substitutions are logged.',
        'Full-resolution geometry is passed without full-frame payload allocation or pixel processing.',
        'JPEG marker scanning does not establish progressive decode compatibility.',
        'No LUT, entropy coding, DMA, SD write, metadata persistence or device validation.',
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS:', ', '.join(f'{len(rows)} {name}' for name, rows in groups.items()))
    print('Total:', sum(map(len, groups.values())), 'bounded cases; no camera or JPEG pixel processing.')


if __name__ == '__main__':
    main()
