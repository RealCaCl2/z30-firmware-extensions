"""Bounded native execution of the upper JPEG decode chain that feeds effect input.

Executes stock 0x10340766 mode 3 -> 0x10340440 -> 0x10390DCC with replaced
codec/event/cache boundaries, the stock plane binding block 0x10344F82 ->
0x103441A0 -> 0x103917F6/0x103917A2, the stock 0x1034080A rounding tail, the
stock upper error checks around 0x10345012/0x103450D0, the stock descriptor
suffix 0x103450DC and the stock effect initializer 0x1038F91E over one shared
stack frame. Synthetic frames only; codec, event, cache, pool and lookup
boundaries are replaced and every replacement is recorded. No pixel decode,
camera access or firmware output.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM, STACK, STOP
from trace_lut_retouch import word, put, returning
from trace_lut_buffers import registers
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
    UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
    UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

CONFIG = 0xC295C2E8         # 0xC295C230+0xB8, codec config cleared by 0x10390DCC
OBJECT_CACHE = 0xC29664E0   # +0x18/+0x1C image object cache, +0x28/+0x2C source cache
GLOBAL_OBJECT = 0xC29B3498  # 0xC29B30D8+0x3C0 global image object slot
STATUS_FLAG = 0xC18CA75C    # +0x14 cleared at 0x1034486E, gates effect submission
BACKEND = 0xC295BD18        # image context handle consumed by 0x1038F64A
CALLBACK = 0x1034001D       # codec completion callback stored at frame+0xC

SOURCE = RAM + 0x40000      # synthetic compressed JPEG source address
SOURCE_PTR = RAM + 0x48000  # [[SOURCE_DESC+4]] == SOURCE double indirection
SOURCE_DESC = RAM + 0x41000 # descriptor returned by 0x103A2070/0x10146DB0 lookups
OBJECT_A = RAM + 0x42000    # pooled/reused image object (planes at +0x2C/+0x30)
OBJECT_B = RAM + 0x43000    # freshly allocated image object
BIG_CONTEXT = RAM + 0x44000 # stock second argument (metadata/context block)
METADATA = RAM + 0x45000
PAIR = RAM + 0x46000        # {payload, length} referenced by image object +4
PAYLOAD = RAM + 0x47000
IMAGE_CONTEXT = RAM + 0x49000
REQUEST = RAM + 0x4A000
HANDLE = RAM + 0x4B000
STATE = STACK + 0xE0058     # stock frame +0x58 (state, 0x78 bytes)
PREPARATION = STACK + 0xE0020  # stock frame +0x20 (preparation, 0x38 bytes)
FRAME = STACK + 0xE0000
Y_PLANE, C_PLANE = RAM + 0x20000, RAM + 0x30000
OPTIONS = RAM + 0x4C000     # options structure pointer stored at request +0x2C


def align(value, boundary):
    return (value + boundary - 1) & ~(boundary - 1)


def map_fixture(c):
    c.map(CONFIG, 0x80)
    c.map(OBJECT_CACHE, 0x40)
    c.map(GLOBAL_OBJECT, 8)
    c.map(BACKEND, 0x400)


def plant_objects(u):
    for block in (SOURCE_DESC, OBJECT_A, OBJECT_B, BIG_CONTEXT, METADATA, PAIR, IMAGE_CONTEXT, REQUEST):
        u.mem_write(block, bytes(0x200))
    put(u, SOURCE_PTR, SOURCE)
    put(u, SOURCE_DESC + 4, SOURCE_PTR)
    put(u, OBJECT_A + 0x2C, Y_PLANE)
    put(u, OBJECT_A + 0x30, C_PLANE)
    put(u, OBJECT_B + 0x2C, Y_PLANE + 0x1000)
    put(u, OBJECT_B + 0x30, C_PLANE + 0x1000)
    put(u, PAIR, PAYLOAD)
    put(u, PAIR + 4, 0x2000)


def build_preparation(u, mode, width, height, source=True):
    u.mem_write(STATE, bytes(0x78))
    u.mem_write(PREPARATION, bytes(0x38))
    u.mem_write(PREPARATION + 6, struct.pack('<H', mode))
    if source:
        put(u, PREPARATION + 0xC, SOURCE)
        put(u, PREPARATION + 0x10, SOURCE_DESC)
        put(u, PREPARATION + 0x14, SOURCE_DESC)
    u.mem_write(PREPARATION + 0x2E, struct.pack('<2H', align(width, 16), align(height, 8)))
    put(u, PREPARATION + 0x18, RAM + 0x34000)
    put(u, PREPARATION + 0x20, Y_PLANE)
    put(u, PREPARATION + 0x24, C_PLANE)


def chain_hooks(c, events, ctrl):
    def boundary(uc, address, size, unused):
        if address == 0x10204B48:
            events.append(dict(kind='cache_invalidate_replaced',
                               address=uc.reg_read(UC_ARM_REG_R0),
                               length=uc.reg_read(UC_ARM_REG_R1)))
            returning(uc, 0)
        elif address == 0x103400E0:
            events.append(dict(kind='event_clear_replaced', mask=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, 0)
        elif address == 0x102193D2:
            frame = uc.reg_read(UC_ARM_REG_R0)
            config = word(uc, frame + 0x14)
            events.append(dict(kind='codec_submit_replaced', callback=word(uc, frame + 0xC),
                               code=word(uc, frame + 0x10), config=hex(config),
                               reserved=word(uc, frame + 0x18)))
            uc.mem_write(config + 0x2C, bytes([ctrl['status']]))
            uc.mem_write(config + 0x2E, struct.pack('<2H', *ctrl['codec_dims']))
            returning(uc, 0)
        elif address == 0x103400D0:
            events.append(dict(kind='event_wait_replaced', mask=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, ctrl['wait'])
        elif address == 0x10390E6E:
            frame = uc.reg_read(UC_ARM_REG_R0)
            events.append(dict(kind='mode0_wrapper_replaced', source=word(uc, frame),
                               info=word(uc, frame + 4), dims=word(uc, frame + 8),
                               formats=bytes(uc.mem_read(frame + 0xC, 2)).hex(),
                               target=word(uc, frame + 0x10)))
            returning(uc, 1)
        elif address == 0x103406CC:
            events.append(dict(kind='mode25_wrapper_replaced', state=uc.reg_read(UC_ARM_REG_R0),
                               preparation=uc.reg_read(UC_ARM_REG_R1)))
            returning(uc, 0)
    c.u.hook_add(UC_HOOK_CODE, boundary)


def decode_chain(data):
    """0x10340766 mode 3: wrapper fields, codec config, status/wait contract."""
    events, ctrl = [], {}
    c = Context(data, data)
    map_fixture(c)
    chain_hooks(c, events, ctrl)
    plant_objects(c.u)
    u = c.u
    rows = []

    def run(width, height, status=0, wait=0x10):
        build_preparation(u, 3, width, height)
        u.mem_write(CONFIG, bytes(0x80))
        ctrl.update(status=status, wait=wait, codec_dims=(32, 16))
        events.clear()
        result = c.call(0x10340766, (STATE, PREPARATION, BIG_CONTEXT))
        assert events == [
            dict(kind='cache_invalidate_replaced', address=Y_PLANE, length=0x3FC300),
            dict(kind='event_clear_replaced', mask=0x90),
            dict(kind='codec_submit_replaced', callback=CALLBACK, code=0x42181802,
                 config=hex(CONFIG), reserved=0),
            dict(kind='event_wait_replaced', mask=0x90)], events
        failed = int(status == 1 or (wait & 0x80) != 0)
        assert result == failed and u.mem_read(STATE + 0xE, 1)[0] == failed
        assert word(u, CONFIG + 0x10) == SOURCE and word(u, CONFIG + 0x14) == 0
        assert [word(u, CONFIG + x) for x in (0x18, 0x1C)] == [align(width, 16)] * 2
        # 0x10340440 hardcodes the wrapper format byte to 2 -> codec format 1.
        assert u.mem_read(CONFIG + 0x20, 1)[0] == 1
        assert [word(u, CONFIG + x) for x in (0x24, 0x28)] == [Y_PLANE, C_PLANE]
        assert word(u, CONFIG) == 0 and word(u, CONFIG + 4) == 0
        assert u.mem_read(CONFIG + 0x2C, 1)[0] == status
        return dict(width=width, height=height, wrapper_format_byte=2, codec_status=status,
                    wait_return=hex(wait), result=result, state_error=u.mem_read(STATE + 0xE, 1)[0],
                    codec_format=u.mem_read(CONFIG + 0x20, 1)[0],
                    strides=[word(u, CONFIG + 0x18), word(u, CONFIG + 0x1C)],
                    compressed_source=hex(word(u, CONFIG + 0x10)),
                    target_planes=[hex(Y_PLANE), hex(C_PLANE)],
                    invalidate=[hex(Y_PLANE), hex(0x3FC300)], events=list(events))

    for width, height in ((32, 16), (5568, 3712), (33, 17)):
        rows.append(run(width, height))
    for status in (1, 2, 0xFF):
        rows.append(run(32, 16, status=status))
    for wait in (0x90, 0x00, 0x80):
        rows.append(run(32, 16, wait=wait))
    rows.append(run(32, 16, status=1, wait=0x90))
    return rows


def codec_wrapper_formats(data):
    """0x10390DCC called directly: wrapper format byte -> codec format mapping."""
    events, ctrl = [], {}
    c = Context(data, data)
    map_fixture(c)
    chain_hooks(c, events, ctrl)
    plant_objects(c.u)
    u = c.u
    rows = []
    for fmt, expected in ((2, 1), (3, 2), (0, 1)):
        param = RAM + 0x12000
        u.mem_write(param, bytes(0x18))
        put(u, param + 0, SOURCE)
        u.mem_write(param + 8, struct.pack('<2H', 32, 16))
        u.mem_write(param + 0xC, bytes([fmt]))
        put(u, param + 0x10, Y_PLANE)
        put(u, param + 0x14, C_PLANE)
        u.mem_write(CONFIG, bytes(0x80))
        ctrl.update(status=0, wait=0x10, codec_dims=(32, 16))
        events.clear()
        result = c.call(0x10390DCC, (param,))
        assert result == 1 and u.mem_read(CONFIG + 0x20, 1)[0] == expected
        assert word(u, CONFIG + 0x10) == SOURCE
        assert [word(u, CONFIG + x) for x in (0x18, 0x1C)] == [32, 32]
        assert [word(u, CONFIG + x) for x in (0x24, 0x28)] == [Y_PLANE, C_PLANE]
        rows.append(dict(wrapper_format_byte=fmt, codec_format=expected, result=result,
                         compressed_source=hex(word(u, CONFIG + 0x10)),
                         strides=[word(u, CONFIG + 0x18), word(u, CONFIG + 0x1C)],
                         target_planes=[hex(Y_PLANE), hex(C_PLANE)],
                         events=[e['kind'] for e in events]))
    return rows


def mode_dispatch(data):
    """Modes other than 3 must not reach the decode wrapper."""
    events, ctrl = [], {}
    c = Context(data, data)
    map_fixture(c)
    chain_hooks(c, events, ctrl)
    plant_objects(c.u)
    u = c.u
    rows = []
    for mode, with_metadata in ((0, True), (0, False), (2, True), (5, True),
                                (1, True), (4, True), (7, True)):
        build_preparation(u, mode, 32, 16)
        u.mem_write(BIG_CONTEXT + 0x22C, bytes([0xAB]))
        u.mem_write(BIG_CONTEXT + 0x22D, bytes([0xCD]))
        events.clear()
        result = c.call(0x10340766, (STATE, PREPARATION, BIG_CONTEXT if with_metadata else 0))
        assert u.mem_read(STATE + 0xE, 1)[0] == 0
        assert not any(e['kind'] == 'codec_submit_replaced' for e in events)
        if mode == 0 and with_metadata:
            assert [e['kind'] for e in events] == ['mode0_wrapper_replaced'], events
            assert events[0]['formats'] == 'cdab'
            reached = 'mode0 wrapper 0x10390E6E'
        elif mode in (2, 5):
            assert [e['kind'] for e in events] == ['mode25_wrapper_replaced'], events
            assert events[0]['preparation'] == PREPARATION
            reached = 'wrapper 0x103406CC'
        else:
            assert events == [], events
            reached = 'no call'
        rows.append(dict(mode=mode, metadata_present=with_metadata, result=hex(result & 0xFFFFFFFF),
                         state_error=0, reached=reached, events=list(events)))
    return rows


def binding_hooks(c, events, ctrl):
    def boundary(uc, address, size, unused):
        if address == 0x103A2070:
            events.append(dict(kind='source_info_lookup_replaced',
                               kind_byte=uc.reg_read(UC_ARM_REG_R0), handle=uc.reg_read(UC_ARM_REG_R1)))
            returning(uc, SOURCE_DESC)
        elif address == 0x10146DB0:
            events.append(dict(kind='source_descriptor_lookup_replaced',
                               handle=uc.reg_read(UC_ARM_REG_R0), id=hex(uc.reg_read(UC_ARM_REG_R1)),
                               context=uc.reg_read(UC_ARM_REG_R2)))
            returning(uc, SOURCE_DESC)
        elif address == 0x10142A78:
            if ctrl.get('native_alloc'):
                return
            events.append(dict(kind='pool_object_alloc_replaced', owner=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, ctrl.get('object', OBJECT_A))
        elif address == 0x10143A30:
            events.append(dict(kind='fresh_object_alloc_replaced', owner=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, OBJECT_B)
        elif address == 0x10141C94:
            events.append(dict(kind='pool_query_replaced', pool=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, ctrl.get('pool_available', 1))
        elif address == 0x100CC512:
            events.append(dict(kind='pool_alloc_replaced', owner=uc.reg_read(UC_ARM_REG_R0),
                               pool=uc.reg_read(UC_ARM_REG_R1), size=hex(uc.reg_read(UC_ARM_REG_R2))))
            returning(uc, ctrl.get('object', OBJECT_A))
        elif address == 0x1014237A:
            events.append(dict(kind='descriptor_init_replaced', object=uc.reg_read(UC_ARM_REG_R0),
                               metadata=uc.reg_read(UC_ARM_REG_R1)))
            returning(uc, 0)
        elif address == 0x10204B7C:
            events.append(dict(kind='object_buffer_clean_invalidate_replaced',
                               payload=hex(uc.reg_read(UC_ARM_REG_R0)),
                               length=uc.reg_read(UC_ARM_REG_R1)))
            returning(uc, 0)
    c.u.hook_add(UC_HOOK_CODE, boundary)


def run_binding_block(c, events, width=32, height=16):
    """Stock block 0x10344F82-0x10344FC6: source lookup + 0x103441A0 plane binding."""
    u = c.u
    put(u, REQUEST + 0, HANDLE)
    put(u, REQUEST + 0x2C, OPTIONS)
    u.mem_write(REQUEST + 0x58, struct.pack('<2H', width, height))
    u.mem_write(PREPARATION, bytes(0x38))
    u.mem_write(FRAME, bytes(0x140))
    put(u, FRAME + 0xD0, IMAGE_CONTEXT)
    events.clear()
    u.reg_write(UC_ARM_REG_R4, REQUEST)
    u.reg_write(UC_ARM_REG_R7, BIG_CONTEXT)
    u.reg_write(UC_ARM_REG_SP, FRAME)
    u.reg_write(UC_ARM_REG_LR, STOP | 1)
    u.emu_start(0x10344F83, 0x10344FC6, count=1000)
    assert u.reg_read(UC_ARM_REG_PC) == 0x10344FC6
    source = word(u, PREPARATION + 0xC)
    planes = [word(u, PREPARATION + x) for x in (0x20, 0x24)]
    global_object = word(u, GLOBAL_OBJECT)
    assert word(u, PREPARATION + 0x10) == SOURCE_DESC == word(u, PREPARATION + 0x14)
    assert source == SOURCE
    assert planes == [Y_PLANE, C_PLANE]
    assert word(u, OBJECT_CACHE + 0x28) == SOURCE_DESC and u.mem_read(OBJECT_CACHE + 0x2C, 1)[0] == 2
    assert word(u, FRAME + 0xC) == OBJECT_A
    assert word(u, FRAME + 0x118) == global_object
    assert [word(u, REQUEST + x) for x in (0x5C, 0x60)] == \
        [word(u, global_object + x) for x in (0x2C, 0x30)]
    assert list(struct.unpack('<2H', bytes(u.mem_read(FRAME + 0x52, 4)))) == [width, height]
    assert u.mem_read(REQUEST + 0xC, 1)[0] == 0
    return dict(source=hex(source), planes=[hex(x) for x in planes],
                tracked_object=hex(global_object), sp52_sp54=[width, height],
                events=list(events))


def object_binding(data):
    """0x103441A0 contract: planes from pooled object, tracked object from global slot."""
    rows = []
    for label, global_value in (('global_object_reused', OBJECT_A),
                                ('global_object_empty_reallocation', 0)):
        events, ctrl = [], {}
        c = Context(data, data)
        map_fixture(c)
        binding_hooks(c, events, ctrl)
        plant_objects(c.u)
        put(c.u, GLOBAL_OBJECT, global_value)
        row = run_binding_block(c, events)
        row.update(case=label, events=[e for e in row['events']])
        if label.endswith('reallocation'):
            assert any(e['kind'] == 'fresh_object_alloc_replaced' for e in row['events'])
            assert c.u.mem_read(OBJECT_B + 0x14, 1)[0] == 0
            assert word(c.u, GLOBAL_OBJECT) == OBJECT_B
            assert row['tracked_object'] == hex(OBJECT_B)
        else:
            assert not any(e['kind'] == 'fresh_object_alloc_replaced' for e in row['events'])
        assert row['planes'] == [hex(Y_PLANE), hex(C_PLANE)]
        row['planes_owner'] = hex(OBJECT_A)
        rows.append(row)
    return rows


def decode_connection(data):
    """One shared frame: binding -> decode -> error checks -> descriptor -> initializer."""
    rows = []
    for width, height, operation in ((32, 16, 5), (33, 17, 6), (5568, 3712, 7)):
        events, ctrl = [], {}
        c = Context(data, data)
        map_fixture(c)
        chain_hooks(c, events, ctrl)
        binding_hooks(c, events, ctrl)
        plant_objects(c.u)
        u = c.u
        put(u, REQUEST + 8, 0)
        u.mem_write(REQUEST + 9, bytes([operation]))
        u.mem_write(REQUEST + 0xA, bytes([0]))
        put(u, GLOBAL_OBJECT, OBJECT_A)
        put(u, BACKEND, IMAGE_CONTEXT)
        put(u, BACKEND + 0x3A4, METADATA)
        put(u, IMAGE_CONTEXT + 0x3A4, METADATA)
        u.mem_write(METADATA + 0x2658, bytes([operation]))

        binding = run_binding_block(c, events, width, height)

        # Stock 0x1034096A computes preparation+6 from preparation+4/+9; the save
        # route resolves to mode 3 (0x10344A54 writes it directly), preset here.
        u.mem_write(PREPARATION + 6, struct.pack('<H', 3))

        # Stock 0x1034080A tail: photo dimensions rounded into preparation before decode.
        u.mem_write(PREPARATION + 0x32, struct.pack('<2H', width, height))
        for reg, value in ((UC_ARM_REG_R4, PREPARATION), (UC_ARM_REG_R5, STATE),
                           (UC_ARM_REG_SP, STACK + 0xF0000), (UC_ARM_REG_LR, STOP | 1)):
            u.reg_write(reg, value)
        u.emu_start(0x103408A3, 0x103408B0, count=100)
        assert u.reg_read(UC_ARM_REG_PC) == 0x103408B0
        assert list(struct.unpack('<2H', bytes(u.mem_read(PREPARATION + 0x2E, 4)))) == \
            [align(width, 16), align(height, 8)]

        ctrl.update(status=0, wait=0x10, codec_dims=(32, 16))
        result = c.call(0x10340766, (STATE, PREPARATION, BIG_CONTEXT))
        assert result == 0 and u.mem_read(STATE + 0xE, 1)[0] == 0
        config_y, config_c = word(u, CONFIG + 0x24), word(u, CONFIG + 0x28)
        assert [config_y, config_c] == [Y_PLANE, C_PLANE]
        decode_stride = word(u, CONFIG + 0x18)

        # Stock upper checks 0x10345012 -> suffix 0x103450DC, stop inside 0x1038F64A.
        put(u, STATUS_FLAG + 0x14, 0)
        put(u, FRAME + 0x18, FRAME + 0x4E)
        put(u, FRAME + 0x110, FRAME + 0x52)
        backend_calls = []

        def boundary(uc, address, size, unused):
            if address == 0x1038F64A:
                backend_calls.append(dict(arguments=registers(uc, 4),
                                          options=word(uc, uc.reg_read(UC_ARM_REG_SP))))
                uc.emu_stop()
            elif address == 0x10341786:
                events.append(dict(kind='commit_call_replaced'))
                returning(uc, 0)
            elif address == 0x103484F6:
                events.append(dict(kind='context_call_replaced'))
                returning(uc, 0)
        u.hook_add(UC_HOOK_CODE, boundary)
        for reg, value in ((UC_ARM_REG_R4, REQUEST), (UC_ARM_REG_R5, 1), (UC_ARM_REG_R6, 0),
                           (UC_ARM_REG_R7, BIG_CONTEXT), (UC_ARM_REG_R8, 3), (UC_ARM_REG_R9, 0),
                           (UC_ARM_REG_R10, STATUS_FLAG), (UC_ARM_REG_R11, 2),
                           (UC_ARM_REG_SP, FRAME), (UC_ARM_REG_LR, STOP | 1)):
            u.reg_write(reg, value)
        u.emu_start(0x10345013, STOP, count=2000)
        assert u.reg_read(UC_ARM_REG_PC) == 0x1038F64A
        assert len(backend_calls) == 1
        call = backend_calls[0]
        desc = FRAME + 0xF0
        assert call['arguments'] == [IMAGE_CONTEXT, desc, 0, operation]
        assert call['options'] == OPTIONS
        assert u.mem_read(desc, 1)[0] == 1
        assert list(struct.unpack('<6H', bytes(u.mem_read(desc + 2, 12)))) == \
            [align(width, 16)] * 3 + [align(height, 8), width, height]
        assert list(struct.unpack('<2H', bytes(u.mem_read(desc + 0xE, 4)))) == [0, 0]
        assert [word(u, desc + x) for x in (0x14, 0x18)] == [Y_PLANE, C_PLANE]
        assert decode_stride == align(width, 16) == \
            struct.unpack('<H', bytes(u.mem_read(desc + 2, 2)))[0]

        params = c.call(0x1038F91E, (BACKEND, desc, OPTIONS))
        assert word(u, BACKEND + 0xC) == params
        assert word(u, params + 4) == desc
        rows.append(dict(width=width, height=height, operation=operation,
                         preparation_stride=align(width, 16), decode_stride=decode_stride,
                         compressed_source=hex(word(u, CONFIG + 0x10)),
                         decode_targets=[hex(config_y), hex(config_c)],
                         descriptor=hex(desc), descriptor_planes=[hex(Y_PLANE), hex(C_PLANE)],
                         effect_input=hex(word(u, params + 4)), params=hex(params),
                         same_plane_objects=[config_y, config_c] == [Y_PLANE, C_PLANE]
                         and word(u, params + 4) == desc,
                         binding=binding, events=list(events)))
    return rows


def failure_hooks(c, events):
    def boundary(uc, address, size, unused):
        if address == 0x10211F5E:
            events.append(dict(kind='image_context_release_replaced',
                               handle=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, 0)
        elif address == 0x1039168C:
            events.append(dict(kind='source_release_replaced'))
            returning(uc, 0)
        elif address == 0x10143AE8:
            events.append(dict(kind='global_object_release_replaced',
                               object=uc.reg_read(UC_ARM_REG_R0)))
            returning(uc, 0)
        elif address == 0x1038F64A:
            events.append(dict(kind='backend_entered', arguments=registers(uc, 4)))
            returning(uc, 1)
        elif address == 0x1038F91E:
            events.append(dict(kind='initializer_entered'))
            returning(uc, 0)
    c.u.hook_add(UC_HOOK_CODE, boundary)


def run_failure_block(c, start, stop_at=0x1034494A, count=500):
    u = c.u
    for reg, value in ((UC_ARM_REG_R4, REQUEST), (UC_ARM_REG_R5, 1), (UC_ARM_REG_R6, 0),
                       (UC_ARM_REG_R7, BIG_CONTEXT), (UC_ARM_REG_R8, 3), (UC_ARM_REG_R9, 0),
                       (UC_ARM_REG_R10, STATUS_FLAG), (UC_ARM_REG_R11, 2),
                       (UC_ARM_REG_SP, FRAME), (UC_ARM_REG_LR, STOP | 1)):
        u.reg_write(reg, value)
    u.emu_start(start | 1, stop_at, count=count)
    return u.reg_read(UC_ARM_REG_PC)


def failure_paths(data):
    """Upper error jumps: releases run, request+0xC set, effect backend never entered."""
    scenarios = [
        ('decode_state_error', 0x10345012, dict(state_error=1), dict(op=5, kind=0, flag=0), 1),
        ('preparation_state_error', 0x10344FF4, dict(state_error=2), dict(op=5, kind=0, flag=0), 2),
        ('global_flag_kind1', 0x1034501C, dict(flag=1), dict(op=5, kind=1, flag=1), 3),
        ('global_flag_before_backend', 0x103450D0, dict(flag=1, global_object=0),
         dict(op=5, kind=0, flag=1), 3),
        ('global_flag_with_object', 0x103450D0, dict(flag=1, global_object=OBJECT_B),
         dict(op=5, kind=0, flag=1), 3),
        ('backend_failure_cleanup', 0x103450DC, dict(global_object=OBJECT_B),
         dict(op=5, kind=0, flag=0), 1),
    ]
    rows = []
    for label, start, inputs, request_fields, expected_error in scenarios:
        events = []
        c = Context(data, data)
        map_fixture(c)
        failure_hooks(c, events)
        plant_objects(c.u)
        u = c.u
        put(u, REQUEST + 0, HANDLE)
        u.mem_write(REQUEST + 9, bytes([request_fields['op']]))
        u.mem_write(REQUEST + 0xA, bytes([request_fields['kind']]))
        u.mem_write(FRAME, bytes(0x140))
        put(u, FRAME + 0xD0, IMAGE_CONTEXT)
        put(u, FRAME + 0x40, Y_PLANE)
        put(u, FRAME + 0x44, C_PLANE)
        u.mem_write(FRAME + 0x4E, struct.pack('<4H', 64, 16, 32, 16))
        if 'state_error' in inputs:
            u.mem_write(FRAME + 0x66, bytes([inputs['state_error']]))
        put(u, STATUS_FLAG + 0x14, inputs.get('flag', 0))
        put(u, GLOBAL_OBJECT, inputs.get('global_object', 0))
        events.clear()
        assert run_failure_block(c, start) == 0x1034494A
        backend = [e for e in events if e['kind'] in ('backend_entered', 'initializer_entered')]
        releases = [e['handle'] for e in events if e['kind'] == 'image_context_release_replaced']
        assert not any(e['kind'] == 'initializer_entered' for e in events), events
        if label == 'backend_failure_cleanup':
            assert len(backend) == 1 and backend[0]['kind'] == 'backend_entered', events
            assert backend[0]['arguments'] == [IMAGE_CONTEXT, FRAME + 0xF0, 0, 5]
        else:
            assert not backend, events
        assert releases == [IMAGE_CONTEXT], events
        error = u.mem_read(REQUEST + 0xC, 1)[0]
        assert error == expected_error, (label, error)
        rows.append(dict(case=label, entry=hex(start), request_error=error,
                         releases=[hex(x) for x in releases],
                         global_release=[e.get('object') for e in events
                                         if e['kind'] == 'global_object_release_replaced'],
                         source_released=any(e['kind'] == 'source_release_replaced' for e in events),
                         backend_events=backend))
        if label == 'global_flag_with_object':
            assert rows[-1]['global_release'] == [OBJECT_B] and word(u, GLOBAL_OBJECT) == 0
    return rows


def object_allocation(data):
    """0x103917A2/0x103917F6 caching over 0x10142A78 pool allocation."""
    events, ctrl = [], {}
    c = Context(data, data)
    map_fixture(c)
    binding_hooks(c, events, ctrl)
    plant_objects(c.u)
    u = c.u
    rows = []

    def reset(**ctrl_update):
        u.mem_write(OBJECT_CACHE, bytes(0x40))
        u.mem_write(OBJECT_A + 0x14, bytes(4))
        u.mem_write(BIG_CONTEXT + 0xED, bytes(1))
        ctrl.clear()
        ctrl.update(ctrl_update)
        events.clear()

    reset()
    u.mem_write(OBJECT_CACHE + 0x1C, bytes([2]))
    put(u, OBJECT_CACHE + 0x18, OBJECT_A)
    result = c.call(0x103917A2, (HANDLE, BIG_CONTEXT))
    assert result == OBJECT_A and not events
    assert u.mem_read(OBJECT_CACHE + 0x1C, 1)[0] == 2
    rows.append(dict(case='cached_object', result=hex(result), events=[]))

    reset()
    result = c.call(0x103917A2, (HANDLE, BIG_CONTEXT))
    assert result == OBJECT_A
    assert [e['kind'] for e in events] == ['pool_object_alloc_replaced'], events
    assert word(u, OBJECT_CACHE + 0x18) == OBJECT_A and u.mem_read(OBJECT_CACHE + 0x1C, 1)[0] == 2
    assert u.mem_read(OBJECT_A + 0x14, 1)[0] == 0
    rows.append(dict(case='uncached_stub_alloc', result=hex(result),
                     events=[e['kind'] for e in events]))

    reset(native_alloc=True, pool_available=1, object=OBJECT_A)
    put(u, OBJECT_A + 4, PAIR)
    result = c.call(0x103917A2, (HANDLE, BIG_CONTEXT))
    assert result == OBJECT_A
    kinds = [e['kind'] for e in events]
    assert kinds == ['pool_query_replaced', 'pool_alloc_replaced', 'descriptor_init_replaced',
                     'object_buffer_clean_invalidate_replaced'], events
    assert events[0]['pool'] == 8 and events[1]['pool'] == 8 and events[1]['size'] == hex(0)
    assert events[3]['payload'] == hex(PAYLOAD) and events[3]['length'] == 0x2000
    assert u.mem_read(OBJECT_CACHE + 0x1C, 1)[0] == 2 and u.mem_read(OBJECT_A + 0x14, 1)[0] == 0
    rows.append(dict(case='native_pool_allocation', result=hex(result),
                     pool=events[0]['pool'], alloc_pool=events[1]['pool'],
                     buffer_maintenance=[events[3]['payload'], events[3]['length']],
                     events=kinds))

    reset(native_alloc=True, pool_available=0, object=0)
    result = c.call(0x103917A2, (HANDLE, BIG_CONTEXT))
    assert result == 0 and [e['kind'] for e in events] == ['pool_query_replaced']
    assert word(u, OBJECT_CACHE + 0x18) == 0 and u.mem_read(OBJECT_CACHE + 0x1C, 1)[0] == 0
    rows.append(dict(case='pool_unavailable_no_fallback', result=hex(result),
                     events=[e['kind'] for e in events]))

    reset(native_alloc=True, pool_available=0, object=OBJECT_A)
    u.mem_write(BIG_CONTEXT + 0xED, bytes([1]))
    result = c.call(0x103917A2, (HANDLE, BIG_CONTEXT))
    assert result == OBJECT_A
    assert events[1]['pool'] == 16 and events[1]['size'] == hex(0x276C000), events
    assert u.mem_read(OBJECT_CACHE + 0x1C, 1)[0] == 2
    rows.append(dict(case='pool_unavailable_fallback16', result=hex(result),
                     alloc_pool=events[1]['pool'], alloc_size=events[1]['size'],
                     events=[e['kind'] for e in events]))

    reset()
    first = c.call(0x103917F6, (HANDLE, BIG_CONTEXT))
    assert [e['kind'] for e in events] == ['source_descriptor_lookup_replaced'], events
    assert events[0]['id'] == hex(0x3A) and events[0]['context'] == BIG_CONTEXT
    assert first == SOURCE
    assert word(u, OBJECT_CACHE + 0x28) == SOURCE_DESC and u.mem_read(OBJECT_CACHE + 0x2C, 1)[0] == 2
    events.clear()
    second = c.call(0x103917F6, (HANDLE, BIG_CONTEXT))
    assert second == SOURCE and events == []
    rows.append(dict(case='source_descriptor_cache', first=hex(first), second=hex(second),
                     lookup_id=hex(0x3A), cached=hex(SOURCE_DESC)))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path,
                   default=Path('build/research/lut-postprocess/decode-chain.json'))
    args = p.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Output must differ from source')
    data = extract_eg(args.source.read_bytes())
    StockImage(data)
    groups = dict(
        decode_chain=decode_chain(data),
        codec_wrapper_formats=codec_wrapper_formats(data),
        mode_dispatch=mode_dispatch(data),
        object_binding=object_binding(data),
        decode_connection=decode_connection(data),
        failure_paths=failure_paths(data),
        object_allocation=object_allocation(data))
    total = sum(len(rows) for rows in groups.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_sha256=sha256(data), **groups,
        limitations=[
            'Synthetic preparation/state/request frames; codec, event, cache, pool and lookup boundaries replaced and recorded.',
            'Preparation mode preset to 3; stock variant selection 0x10340920/0x1034080A is not executed end to end, only its 0x10340486 rounding tail.',
            'Upper prologue 0x10344828 not executed: frame fields sp+0x18/0x110 and r5/r6/r8/r9/r10/r11 preset to prologue-derived constants.',
            '0x1014237A descriptor initialization replaced; image object +0x2C/+0x30 planes preset by the fixture.',
            '0x103A2070/0x10146DB0 source lookups replaced; the compressed source is observed through the stock [[desc+4]] indirection only.',
            'Codec submit writes synthetic status/dims; no pixel decode, no real JPEG, no camera, no firmware output.',
            'Backend 0x1038F64A is a recorded boundary; metadata serialization and SD writing remain out of scope.']),
        indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {total} bounded decode-chain cases across {len(groups)} groups; no pixel decode.')


if __name__ == '__main__':
    main()
