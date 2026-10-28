"""Offline experiment with stock still-image extraction and chunk writer.

Input descriptors and encoded image bytes are synthetic. File writes are
intercepted into a Python sink; no camera, SD card, capture, task or DMA model.
This deliberately does not generate an installable pre-capture patch.
"""
import argparse
import hashlib
import io
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_PC, UC_ARM_REG_LR,
)

EXTRACT = 0x102B688E
ORDER = 0x102B68E0
SEGMENT_COUNT = 0x102B6FBE
WRITE = 0x102B6EC0
FILE_BOUNDARY = 0x102C8DC6
CHUNK_LIMIT = 0x100000
OBJ, BANKS, HEADER = RAM + 0x1000, RAM + 0x1100, RAM + 0x1200
PRIMARY, SECONDARY, TRAILER = RAM + 0x1300, RAM + 0x1400, RAM + 0x1500
RESULT = RAM + 0x2000
PAYLOAD = RAM + 0x200000
COPY = RAM + 0x600000


def digest(data):
    return hashlib.sha256(data).hexdigest()


class WriterFixture:
    def __init__(self, stock):
        self.context = Context(stock, stock)
        self.output = bytearray()
        self.calls = []
        self.failure_call = None
        self.failure_code = 0
        self.short_success = False
        self.context.u.hook_add(UC_HOOK_CODE, self.file_write,
                                begin=FILE_BOUNDARY, end=FILE_BOUNDARY)

    def word(self, address, value):
        self.context.u.mem_write(address, struct.pack('<I', value))

    def file_write(self, u, address, size, unused):
        fd, pointer, count, actual = [u.reg_read(r) for r in (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        self.calls.append(dict(fd=fd, address=hex(pointer), size=count))
        if len(self.calls) == self.failure_call:
            self.word(actual, 0)
            u.reg_write(UC_ARM_REG_R0, self.failure_code)
        else:
            completed = min(count, 17) if self.short_success else count
            self.output.extend(u.mem_read(pointer, completed))
            self.word(actual, completed)
            u.reg_write(UC_ARM_REG_R0, 0)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def configure(self, parts, address=PAYLOAD):
        """Construct only the recovered synthetic mode-1 descriptor subset.

        This is NOT a constructor for a real Nikon capture or DCF object.
        Optional sections have valid descriptor nodes with zero lengths.
        """
        if len(parts) != 4:
            raise ValueError('Synthetic mode-1 fixture has four sections')
        for node in (OBJ, BANKS, HEADER, PRIMARY, SECONDARY, TRAILER):
            self.context.u.mem_write(node, bytes(0x60))
        self.word(OBJ + 0x0C, BANKS)
        self.word(OBJ + 0x10, HEADER)
        self.word(BANKS + 0x10, PRIMARY)
        self.word(BANKS + 0x1C, SECONDARY)
        self.word(BANKS + 0x04, TRAILER)
        self.context.u.mem_write(OBJ + 0x18, struct.pack('<Q', sum(map(len, parts))))
        pointers = []
        for index, (node, part) in enumerate(zip((HEADER, PRIMARY, SECONDARY, TRAILER), parts)):
            self.context.map(address, max(1, len(part)))
            if part:
                self.context.u.mem_write(address, part)
            pointers.append(address)
            if index == 0:
                self.word(node, address if part else 0)
                self.word(node + 8, len(part))
            else:
                self.word(node + 8, address)
                self.word(node + 12, len(part))  # No filler padding.
                self.word(OBJ + (0x24, 0x28, 0x2C)[index - 1], len(part))
            address += max(len(part), 4) + 32
        return pointers

    def extract(self, kind, mode=1):
        result = self.context.call(EXTRACT, (mode, OBJ, kind, RESULT))
        descriptor = struct.unpack('<3I', self.context.u.mem_read(RESULT, 12))
        return result, descriptor

    def copy_encoded_sections(self, budget):
        """Copy bytes actually found by native extraction from fixture RAM.

        Real camera frame ownership and concurrent stability are not modeled.
        Filler-backed sections need further reverse engineering and are refused.
        """
        sections = []
        total = 0
        for kind in (0, 5, 6, 3):
            result, (pointer, length, padding) = self.extract(kind)
            if result == 1:
                continue
            if result != 0 or padding or length <= 0 or not pointer:
                raise ValueError('Unsupported or invalid section')
            total += length
            if total > budget:
                raise ValueError('Snapshot exceeds budget')
            sections.append((pointer, length))
        declared = struct.unpack('<Q', self.context.u.mem_read(OBJ + 0x18, 8))[0]
        if not total or total != declared:
            raise ValueError('Segment sum differs from declared file length')
        return b''.join(bytes(self.context.u.mem_read(pointer, length)) for pointer, length in sections)

    def write(self, mode=1, failure_call=None, failure_code=0, short_success=False):
        self.output.clear()
        self.calls.clear()
        self.failure_call, self.failure_code = failure_call, failure_code
        self.short_success = short_success
        result = self.context.call(WRITE, (99, mode, OBJ))
        return result, bytes(self.output), list(self.calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output-dir', type=Path, default=Path('build/research/precapture-writer'))
    args = parser.parse_args()
    source = args.source.read_bytes()
    stock = source if len(source) == 33939514 else extract_eg(source)
    StockImage(stock)
    output = args.output_dir.resolve()
    generated = [output / name for name in ('report.json', 'fixture.jpg', 'delayed-copy.jpg')]
    if args.source.resolve() in generated:
        raise ValueError('Output must not overwrite the source')

    # A real, decodable JPEG, generated locally rather than supplied by a camera.
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 48), '#26384c')
    ImageDraw.Draw(image).rectangle((8, 8, 56, 40), fill='#dabc57')
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=92)
    jpeg = stream.getvalue()
    assert jpeg[:2] == b'\xff\xd8' and jpeg[-2:] == b'\xff\xd9'
    parts = [jpeg[:2], jpeg[2:-2], jpeg[-2:], b'']

    f = WriterFixture(stock)
    cases = []
    mappings = []
    for mode in range(4):
        count = f.context.call(SEGMENT_COUNT, (mode,))
        order = [f.context.call(ORDER, (mode, index)) for index in range(count)]
        assert order == ([0, 5, 6, 3] if mode in (1, 2) else [0, 3, 1, 4, 2, 5])
        mappings.append(dict(mode=mode, count=count, order=order, image_format_bound=False))
    assert f.context.call(SEGMENT_COUNT, (4,)) == 0
    cases.append(dict(name='stock_segment_order', passed=True, modes=mappings))

    pointers = f.configure(parts)
    for kind, part, pointer in zip((0, 5, 6, 3), parts, pointers):
        result, descriptor = f.extract(kind)
        assert result == (0 if part else 1)
        assert descriptor[1:] == (len(part), 0)
        if part:
            assert descriptor[0] == pointer
    cases.append(dict(name='native_segment_pointer_length_extraction', passed=True))
    result, descriptor = f.extract(7)
    assert result == 2 and descriptor == (0, 0, 0)
    result, descriptor = f.extract(0, mode=4)
    assert result == 3 and descriptor == (0, 0, 0)
    cases.append(dict(name='unsupported_kind_and_mode', passed=True))
    result, emitted, calls = f.write(mode=4)
    assert result == 0 and emitted == b'' and not calls
    cases.append(dict(name='unsupported_writer_mode_returns_empty_success', passed=True))

    result, emitted, calls = f.write()
    assert result == 0 and emitted == jpeg and len(calls) == 3
    cases.append(dict(name='synthetic_multipart_jpeg_replayed_by_stock_writer', passed=True,
                      file_sha256=digest(emitted), size=len(emitted), calls=calls))

    # Keep only descriptors, reuse the producer's RAM, then write later.
    # This negative control demonstrates why pointer retention is insufficient.
    snapshot = f.copy_encoded_sections(budget=len(jpeg))
    assert snapshot == jpeg
    try:
        f.copy_encoded_sections(budget=len(jpeg) - 1)
    except ValueError:
        pass
    else:
        raise AssertionError('Oversize copy must be refused')
    cases.append(dict(name='owned_copy_budget_guard', passed=True))
    f.word(OBJ + 0x24, len(parts[1]) + 1)
    try:
        f.copy_encoded_sections(budget=len(jpeg) + 1)
    except ValueError:
        pass
    else:
        raise AssertionError('Unrecovered filler contract must be refused')
    f.word(OBJ + 0x24, len(parts[1]))
    cases.append(dict(name='unrecovered_filler_section_refused', passed=True))
    for pointer, part in zip(pointers, parts):
        if part:
            f.context.u.mem_write(pointer, bytes(len(part)))
    result, stale, calls = f.write()
    assert result == 0 and stale != jpeg and stale == bytes(len(jpeg))
    cases.append(dict(name='shallow_retention_after_producer_reuse', passed=True,
                      corruption_detected=True, native_writer_still_returned_success=True))

    # A separate synthetic allocation owns a byte copy, not the producer pointer.
    # Rebuild the fixture subset to replay that copy through stock instructions.
    f.configure([snapshot, b'', b'', b''], COPY)
    result, delayed, calls = f.write()
    assert result == 0 and delayed == jpeg
    with Image.open(io.BytesIO(delayed)) as decoded:
        decoded.load()
        assert decoded.size == (64, 48)
    cases.append(dict(name='owned_copy_survives_producer_reuse', passed=True,
                      file_sha256=digest(delayed), decoded_size=[64, 48]))

    # Two full chunks plus a tail. This stream is a byte fixture, not a JPEG.
    large = bytes(range(256)) * 8192 + b'last seventeen!!!'
    assert len(large) == 2 * CHUNK_LIMIT + 17
    f.configure([large, b'', b'', b''])
    result, emitted, calls = f.write()
    assert result == 0 and emitted == large
    assert [call['size'] for call in calls] == [CHUNK_LIMIT, CHUNK_LIMIT, 17]
    cases.append(dict(name='native_1mib_chunking_and_tail', passed=True,
                      bytes=len(emitted), calls=calls))

    for failure_call in (1, 2, 3):
        for error, expected in ((0xA0000002, 2), (0xA0000003, 3)):
            result, partial, calls = f.write(failure_call=failure_call, failure_code=error)
            assert result == expected and len(calls) == failure_call
            assert partial == large[:(failure_call - 1) * CHUNK_LIMIT]
            cases.append(dict(name=f'write_error_{error:08x}_at_chunk_{failure_call}',
                              passed=True, returned=result, partial_bytes=len(partial)))

    # Check the service contract assumption using an intentionally short sink.
    # Real 0x102C8DC6 may guarantee completion; its contract is not recovered.
    f.configure([jpeg, b'', b'', b''])
    result, short, calls = f.write(short_success=True)
    assert result == 0 and short == jpeg[:17] and len(calls) == 1
    cases.append(dict(name='success_response_with_short_sink_is_not_validated', passed=True,
                      native_returned_success=True, emitted_bytes=len(short),
                      real_file_service_short_success_supported=None))

    # The recovered writer also trusts total-size input.
    f.configure([jpeg, b'', b'', b''])
    f.word(OBJ + 0x18, len(jpeg) - 1)
    result, emitted, calls = f.write()
    assert result == 0 and emitted == jpeg
    try:
        f.copy_encoded_sections(budget=len(jpeg))
    except ValueError:
        pass
    else:
        raise AssertionError('Inconsistent length must be refused by copy adapter')
    cases.append(dict(name='inconsistent_total_size_is_not_validated', passed=True,
                      input_total=len(jpeg) - 1, emitted_bytes=len(emitted)))

    output.mkdir(parents=True, exist_ok=True)
    (output / 'fixture.jpg').write_bytes(jpeg)
    (output / 'delayed-copy.jpg').write_bytes(delayed)
    report = dict(
        source_sha256=sha256(stock), experiment='stock still-writer offline probe',
        native_entries={key: hex(value) for key, value in dict(
            extract=EXTRACT, segment_order=ORDER, segment_count=SEGMENT_COUNT,
            writer=WRITE, intercepted_file_boundary=FILE_BOUNDARY).items()},
        cases=cases, passed=len(cases),
        copy_adapter_location='Python fixture, not camera machine code',
        delay_model='Producer RAM is overwritten between snapshot and replay; no timed camera delay',
        synthetic_inputs=['RAM addresses', 'mode-1 descriptor subset', 'generated 64x48 JPEG',
                          'large byte stream', 'file success and error responses'],
        substitutions=['0x102C8DC6 file operation returns fixture responses into Python sink',
                       'native_runtime.Context existing log callback is suppressed'],
        camera_capture_verified=False, jpeg_capture_mode_binding_verified=False,
        live_photo_pool_binding_verified=False, real_capture_object_ownership_verified=False,
        live_free_bytes=None, real_sd_write_verified=False,
        exposure_time_and_exif_binding_verified=False, precapture_feature_implemented=False,
        limitations=[
            'No sensor, DMA, AF, ISP, JPEG encoder, filesystem, DCF or camera task execution.',
            'Mode IDs and segment IDs are not assigned camera format names.',
            'Synthetic copy storage is not verified available memory on the camera.',
            'Replay proves local helper behavior, not a legal delayed capture-object submission.',
            'Original camera frames may own additional metadata and resources not represented here.',
        ],
    )
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=len(cases), output=str(output),
                         native_writer_replayed_fixture=True, camera_capture_verified=False), indent=2))


if __name__ == '__main__':
    main()
