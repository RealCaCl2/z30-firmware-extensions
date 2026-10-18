"""Run built trace code, ring wrap, freeze, menu write and I/O failure fixtures."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))


def verify(stock, image, report):
    from verify_sound_settings import Fixture
    from read_aspect_trace import decode
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                  UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_R12,
                                  UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)
    from z30_extensions.platform.z30_120 import sha256
    feature = report['features']['aspect_profiles']
    functions = {k: int(v, 16) for k,v in feature['functions'].items()}
    files, busy = {}, [0]
    s = Fixture(stock, image, report, files)
    base = s.state + feature['state_offset']
    def property_api(u, address, size, data):
        key = u.reg_read(UC_ARM_REG_R0)
        value = busy[0] if key == 0x188c else 2 if key == 0x501 else 0
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    for addr in (0x10465984, 0x101993de):
        s.u.hook_add(UC_HOOK_CODE, property_api, begin=addr, end=addr)
    # Capture-side record production must never touch the file services.
    before = len(s.calls)
    s.u.mem_write(base, struct.pack('<I', 1))
    payload = 0x50080000
    for index in range(70):
        s.u.mem_write(payload, struct.pack('<8I', 2, 2, 0x1234, 2472 | (1856 << 16), index, 0, 0, 0))
        s.call(functions['aspect_trace_event'], (1, payload, 32, 0x10142c21))
    assert len(s.calls) == before
    length = feature['diagnostic_trace']['bytes']
    parsed = decode(bytes(s.u.mem_read(base+16, length)))
    assert parsed['counter'] == 70
    ring=feature['diagnostic_trace'].get('output_ring_capacity',64)
    assert [r['sequence'] for r in parsed['records']] == list(range(71-ring,71))
    assert parsed['records'][-1]['saved_r4_r7'][0] == hex(69)
    if feature['diagnostic_trace']['version']>=2:
        for caller in (0x10132247,0x100aea9b):
            s.call(functions['aspect_trace_event'],(1,payload,32,caller))
        s.u.mem_write(base,struct.pack('<I',4))
        s.call(functions['aspect_trace_event'],(1,payload,32,0x10000101))
        assert decode(bytes(s.u.mem_read(base+16,length)))['counter']==70
        s.u.mem_write(base,struct.pack('<I',1))
    s.u.mem_write(base+36, struct.pack('<I', 1))
    s.call(functions['aspect_trace_event'], (1, payload, 32, 1))
    assert decode(bytes(s.u.mem_read(base+16,length)))['counter'] == 70
    s.u.mem_write(base+36, bytes(4))
    s.call(functions['aspect_trace_event'], (1, payload, 145, 1))
    assert decode(bytes(s.u.mem_read(base+16,length)))['counter'] == 70
    # The real output hook records the original call arguments, not just a
    # hand-built event. Its returned pointer/dimensions retain the v5 contract.
    pointer = s.call(0x1020bfda, (2,2))
    assert struct.unpack('<2H',s.u.mem_read(pointer,4)) == (2472,1856)
    parsed = decode(bytes(s.u.mem_read(base+16,length)))
    assert parsed['records'][-1]['wh'] == [2472,1856]
    # A native normal-config execution produces a complete event-2 snapshot.
    obj,info,holder = 0x50082000,0x50084000,0x5008a000
    s.u.mem_write(obj,bytes(0x100));s.u.mem_write(info,bytes(0x6000))
    s.u.mem_write(obj+4,struct.pack('<I',holder));s.u.mem_write(holder,struct.pack('<I',0x50090000))
    s.u.mem_write(info+0x208,b'\x02');s.u.mem_write(info+0x229,b'\x02')
    s.call(0x1014237a,(obj,info,0))
    parsed = decode(bytes(s.u.mem_read(base+16,length)))
    config = [r for r in parsed['records'] if r['event']==2][-1]
    if feature.get('narrow_source_window_version'):
        assert config['parent_wh']==[4952,3712] and config['origin_xy']==[324,8]
    else:
        assert config['parent_wh']==[5568,3712] and config['origin_xy']==[16,8]
    if feature['diagnostic_trace']['version']>=2:
        for index in range(100):s.call(functions['aspect_trace_event'],(1,payload,32,0x10000101))
        parsed=decode(bytes(s.u.mem_read(base+16,length)))
        assert config in parsed['records'] # pinned snapshot survives output-ring overwrite
    processing_checks = {}
    if feature['diagnostic_trace']['version'] >= 3:
        from native_runtime import Context, STACK, STOP
        s.u.mem_write(info + 0x2658, b'\x15')
        s.u.mem_write(info + 0x70, struct.pack('<I', 0x12345678))
        s.u.mem_write(info + 0x2941, b'\x01')
        s.call(functions['aspect_trace_config'], (info, obj))
        parsed = decode(bytes(s.u.mem_read(base+16, length)))
        branch = [r for r in parsed['records'] if r['event'] == 2][-1]['capture']
        assert (branch['mode2658'], branch['flag70'], branch['flag2941']) == (0x15, 0x12345678, 1)
        # Compare the complete hooked translator against stock, including all
        # untouched bytes and its ABI, rather than only inspecting the record.
        native = Context(stock, stock)
        scratch, params = 0x500A0000, 0x500A1000
        source = bytearray(0x100)
        source[0xC] = 2
        struct.pack_into('<4I', source, 0x3C, 5568, 3712, 4952, 3712)
        struct.pack_into('<4I', source, 0x4C, 308, 308, 0, 0)
        for context in (s, native):
            context.u.mem_write(scratch, bytes(source))
            context.u.mem_write(params, b'\xA5' * 0x500)
        expected = native.call(0x102507CA, (scratch, params))
        actual = s.call(0x102507CA, (scratch, params))
        assert expected == actual
        assert bytes(s.u.mem_read(params, 0x500)) == bytes(native.u.mem_read(params, 0x500))
        parsed = decode(bytes(s.u.mem_read(base+16, length)))
        parameter_record = [r for r in parsed['records'] if r['event'] == 4][-1]
        assert parameter_record['output_wh'] == [4952, 3712]
        assert parameter_record['margins_lrtb'] == [308, 308, 0, 0]
        counter_before_preview = parsed['counter']
        struct.pack_into('<2I', source, 0x44, 1920, 1080)
        for context in (s, native):
            context.u.mem_write(scratch, bytes(source))
            context.u.mem_write(params, b'\xA5' * 0x500)
        assert s.call(0x102507CA, (scratch, params)) == native.call(0x102507CA, (scratch, params))
        assert bytes(s.u.mem_read(params, 0x500)) == bytes(native.u.mem_read(params, 0x500))
        assert decode(bytes(s.u.mem_read(base+16, length)))['counter'] == counter_before_preview
        # Check entry trampoline replay at the real continuation. It must
        # preserve incoming r1/r2/r3/r12/LR and the original push frame.
        request = 0x500A2000
        request_data = bytearray(72)
        struct.pack_into('<4I', request_data, 0x10, 308, 308, 0, 0)
        struct.pack_into('<4H', request_data, 0x20, 5568, 3712, 4952, 3712)
        stop_at = 0x100EF7C2
        def stop_continuation(u, address, size, data):
            u.emu_stop()
        stop_hook = s.u.hook_add(UC_HOOK_CODE, stop_continuation, begin=stop_at, end=stop_at)
        for width in (4952, 1920):
            struct.pack_into('<H', request_data, 0x24, width)
            s.u.mem_write(request, bytes(request_data))
            old_counter = decode(bytes(s.u.mem_read(base+16, length)))['counter']
            for register, value in ((UC_ARM_REG_R0, request), (UC_ARM_REG_R1, 0x12345678),
                                    (UC_ARM_REG_R2, 0x89ABCDEF), (UC_ARM_REG_R3, 0x31415926),
                                    (UC_ARM_REG_R12, 0x27182818), (UC_ARM_REG_LR, STOP | 1),
                                    (UC_ARM_REG_SP, STACK + 0xF0000)):
                s.u.reg_write(register, value)
            s.u.emu_start(0x100EF7B9, STOP, count=100000)
            assert s.u.reg_read(UC_ARM_REG_PC) == stop_at
            assert s.u.reg_read(UC_ARM_REG_SP) == STACK + 0xF0000 - 36
            assert s.u.reg_read(UC_ARM_REG_R0) == 0x4758
            assert s.u.reg_read(UC_ARM_REG_R5) == request
            for register, value in ((UC_ARM_REG_R1, 0x12345678), (UC_ARM_REG_R2, 0x89ABCDEF),
                                    (UC_ARM_REG_R3, 0x31415926), (UC_ARM_REG_R12, 0x27182818),
                                    (UC_ARM_REG_LR, STOP | 1)):
                assert s.u.reg_read(register) == value
            parsed = decode(bytes(s.u.mem_read(base+16, length)))
            assert parsed['counter'] == old_counter + (width >= 2000)
        s.u.hook_del(stop_hook)
        request_record = [r for r in parsed['records'] if r['event'] == 3][-1]
        assert request_record['output_wh'] == [4952, 3712]
        assert request_record['margins_lrtb'] == [308, 308, 0, 0]
        for index in range(100):
            s.call(functions['aspect_trace_event'], (1, payload, 32, 0x10000101))
        parsed = decode(bytes(s.u.mem_read(base+16, length)))
        assert request_record in parsed['records'] and parameter_record in parsed['records']
        processing_checks = dict(parameter_translation_stock_equivalent=True,
                                 request_entry_abi_verified=True,
                                 capture_branch_selectors_verified=True,
                                 small_preview_request_dropped=True,
                                 small_preview_parameters_dropped=True,
                                 processing_records_survive_output_wrap=True)
    assert not s.opened and len(s.calls)==before
    # Export is deferred to an idle menu task; full file round-trip is parsed.
    s.call(functions['aspect_trace_save'])
    blob = files['A:\\WL-ASPECT.DBG']
    assert len(blob)==length and not s.opened and s.u.mem_read(base+36,4)==bytes(4)
    exported = decode(blob)
    assert exported['counter']==parsed['counter']
    busy[0]=1
    old_calls=len(s.calls)
    s.call(functions['aspect_trace_save'])
    assert len(s.calls)==old_calls and files['A:\\WL-ASPECT.DBG']==blob
    busy[0]=0
    # Both failure fixtures must release trace freeze so subsequent captures
    # can still record. Close-error service ownership remains a model limit.
    for attr in ('short_write','close_error'):
        setattr(s,attr,True)
        s.call(functions['aspect_trace_save'])
        assert s.u.mem_read(base+36,4)==bytes(4)
        setattr(s,attr,False)
        s.opened.clear()
    return {'image_sha256':sha256(image),'ring_capacity_verified':feature['diagnostic_trace']['capacity'],
            'output_hook_verified':True,'native_config_snapshot_verified':True,
            'capture_file_io_calls':0,'deferred_dump_bytes':length,
            'busy_skip_verified':True,'freeze_failure_release_verified':True,
            'physical_capture_verified':False,'physical_card_verified':False,
            **processing_checks}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--deps',type=Path)
    a=p.parse_args()
    if a.deps:sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    result=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),json.loads(a.report.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
