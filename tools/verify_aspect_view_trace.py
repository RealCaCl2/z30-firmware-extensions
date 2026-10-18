"""Check view diagnostics without claiming sensor/LCD or scheduler coverage."""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def verify(stock, image, report, previous, previous_report):
    from verify_sound_settings import Fixture
    from read_aspect_trace import decode, MAGIC
    from z30_extensions.assembly import constant, assemble, jump
    from z30_extensions.platform.z30_120 import sha256
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R7
    latest = {0x10197D9D: 6, 0x1018AB41: 7, 0x101985E5: 8,
              0x10132247: 9, 0x100AEA9B: 10}
    cache, stub, parent, incoming = 0xC237C380, 0x500B0000, 0x500B1000, 0x500B2000

    def setup(binary, metadata):
        c = Fixture(stock, binary, metadata, {}, properties={0x501: 2, 0x189E: 2})
        state = c.state + metadata['features']['aspect_profiles']['state_offset']
        c.u.mem_write(state, struct.pack('<I', 1))
        c.map(cache, 256)
        blob = bytearray(256)
        # Native 10197D78 loads C237C388 for WH, then subtracts eight for
        # rectangle stores. Thus WH are +4E/+50 relative to C237C380.
        struct.pack_into('<2H', blob, 0x4E, 5568, 3712)
        struct.pack_into('<4H', blob, 0x52, 0, 292, 5568, 3420)
        struct.pack_into('<6H', blob, 0x60, 5568, 3712, 0, 0, 5568, 3712)
        c.u.mem_write(cache, bytes(blob))
        c.u.mem_write(parent + 0x52, struct.pack('<2H', 5568, 3712))
        c.u.mem_write(incoming, struct.pack('<2H', 720, 480))
        c.u.mem_write(0x500B4000, bytes([0, 0, 2, 0]))
        c.call(0x10197D78, (0x500B4000,))
        assert struct.unpack('<2H', c.u.mem_read(cache + 0x4E, 4)) == (5568, 3712)
        return c, state

    def query(c, caller):
        # The native continuation hook redirects only this explicit fixture
        # call; no native caller body is claimed to execute here.
        entry = stub + getattr(c, 'query_serial', 0) * 256
        c.query_serial = getattr(c, 'query_serial', 0) + 1
        prefix = f'''push {{r4-r8,lr}};{constant('r4', parent)}
            {constant('r7', incoming)}{constant('lr',caller)}'''
        head = assemble(prefix, entry, 64)
        branch_at = entry + len(head)
        tail = branch_at + 8
        c.u.mem_write(entry, head + jump(branch_at, 0x1020BFDA, 8) + assemble('pop {r4-r8,pc}', tail, 8))
        values = []
        def continuation(u, address, size, unused):
            assert u.reg_read(UC_ARM_REG_R4) == parent and u.reg_read(UC_ARM_REG_R7) == incoming
            ptr = u.reg_read(UC_ARM_REG_R0)
            values.append(list(struct.unpack('<2H', u.mem_read(ptr, 4))))
            u.reg_write(UC_ARM_REG_PC, tail | 1)
        hook = c.u.hook_add(UC_HOOK_CODE, continuation, begin=caller & ~1, end=caller & ~1)
        try:
            try:
                c.call(entry, (2, 0))
            except Exception as error:
                raise AssertionError(f'Query {caller:#x} stopped at PC {c.u.reg_read(UC_ARM_REG_PC):#x}') from error
        finally:
            c.u.hook_del(hook)
        assert len(values) == 1
        return values[0]

    c, base = setup(image, report)
    old, old_base = setup(previous, previous_report)
    feature = report['features']['aspect_profiles']
    f = {k: int(v, 16) for k, v in feature['functions'].items()}
    length = feature['diagnostic_trace']['bytes']
    read = lambda: decode(bytes(c.u.mem_read(base + 16, length)))
    before_io = len(c.calls)
    abi_cases = []
    for profile in (0, 1, 2):
        for caller, event in latest.items():
            for context, state in ((c, base), (old, old_base)):
                context.u.mem_write(state, struct.pack('<I', profile))
            actual, expected = query(c, caller), query(old, caller)
            assert actual == expected
            records = [r for r in read()['records'] if r['profile'] == profile and r['event'] == event]
            assert len(records) == 1 and records[0]['wh'] == actual
            assert records[0]['view_cache']['area_rect'] == [0, 292, 5568, 3420]
            assert records[0]['view_cache']['full_wh'] == [5568, 3712]
            if event >= 9:
                assert records[0]['caller_input_wh'] == ([720, 480] if event == 9 else [5568, 3712])
                first = next(r for r in read()['records'] if r['profile'] == profile and r['event'] == event + 2)
                assert first['wh'] == actual
            counter = read()['counter']
            query(c, caller)
            assert read()['counter'] == counter  # Unchanged geometry is not recorded per frame.
            abi_cases.append(dict(profile=profile, caller=hex(caller), wh=actual))
    c.u.mem_write(base, struct.pack('<I', 1))
    c.call(f['aspect_trace_view_menu'])
    before_log = bytes(c.u.mem_read(base + 16, length))
    originals = {r['event']: r for r in read()['records'] if r['profile'] == 1 and r['event'] >= 9}
    c.u.mem_write(parent + 0x52, struct.pack('<2H', 4952, 3712))
    c.u.mem_write(incoming, struct.pack('<2H', 640, 360))
    for caller, event in latest.items():
        if event >= 9:
            query(c, caller)
            current = next(r for r in read()['records'] if r['profile'] == 1 and r['event'] == event)
            first = next(r for r in read()['records'] if r['profile'] == 1 and r['event'] == event + 2)
            assert current['caller_input_wh'] != originals[event]['caller_input_wh']
            assert first == originals[event + 2]
    c.call(f['aspect_trace_view_menu'])
    menu = next(r for r in read()['records'] if r['event'] == 5)
    assert (menu['user_area'], menu['derived_area']) == (2, 2)
    assert menu['view_cache']['buffer_rect'] == [0, 0, 5568, 3712]
    pinned = [r for r in read()['records'] if r['event'] != 1]
    payload = 0x500B3000
    c.u.mem_write(payload, bytes(32))
    for _ in range(120):
        c.call(f['aspect_trace_event'], (1, payload, 32, 0x10000001))
    assert all(r in read()['records'] for r in pinned)
    assert len(c.calls) == before_io
    c.u.mem_write(base + 36, struct.pack('<I', 1))
    count = read()['counter']
    c.u.mem_write(parent + 0x52, struct.pack('<2H', 4640, 3712))
    query(c, 0x100AEA9B)
    assert read()['counter'] == count
    c.u.mem_write(base + 36, bytes(4))
    c.call(f['aspect_trace_save'])
    assert decode(c.files['A:\\WL-ASPECT.DBG']) == read()
    from compare_aspect_view_trace import compare
    comparison = compare(before_log, c.files['A:\\WL-ASPECT.DBG'])
    assert comparison['any_geometry_difference']
    assert {row['event'] for row in comparison['samples'] if row['changed']} == {9, 10}
    assert not compare(before_log, before_log)['any_geometry_difference']
    for version in (1, 2, 3):
        legacy = struct.pack('<6I', MAGIC, version, 0, 64, 160, 0) + bytes(64 * 160)
        assert decode(legacy)['version'] == version
    return dict(image_sha256=sha256(image), previous_sha256=sha256(previous),
                output_abi_and_geometry_cases=abi_cases, repeated_query_change_filter=True,
                first_hot_snapshots_preserved=True, pinned_view_snapshots_survive_ring=True,
                defined_cache_fields_verified=True, menu_area_snapshot=True,
                callback_file_io_calls=0, export_bytes=length, legacy_decoder_versions=[1,2,3],
                comparison_changed_and_equal_cases=True,
                camera_verified=False,
                limits=['Explicit native-caller fixture; native caller bodies and LCD are not executed.',
                        'Reads cached geometry and two typed caller WH fields, not pixels or MMIO.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    result = verify(extract_eg(a.source.read_bytes()), a.image.read_bytes(), json.loads(a.report.read_text('utf-8')),
                    a.previous_image.read_bytes(), json.loads(a.previous_report.read_text('utf-8')))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f"PASS {len(result['output_abi_and_geometry_cases'])} view query ABI/geometry cases, first/latest snapshots, change filter and export")


if __name__ == '__main__':
    main()
