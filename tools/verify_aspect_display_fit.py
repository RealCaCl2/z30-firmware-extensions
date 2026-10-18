"""Replay the complete native display fit function at the camera-observed query.

Geometry primitives run natively. Arguments are bounded fixtures, not camera
object dumps; no applied LCD transform or ISP pixels are simulated.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def fixture(stock, image, report, profile=0, user_area=2):
    from native_runtime import Context
    from verify_sound_settings import Fixture
    if report is None:
        return Context(stock, stock)
    c = Fixture(stock, image, report, {}, properties={0x501: user_area})
    c.u.mem_write(c.state + report['features']['aspect_profiles']['state_offset'], struct.pack('<I', profile))
    c.map(0xC237C380, 256)
    return c


def fit(c, area=2, viewport=(720, 480), control_wh=(720, 480), flags=(0, 0)):
    arg, control, size, view, ratio, result = [0x50080000 + i * 256 for i in range(6)]
    data = bytearray(32)
    data[0], data[1], data[0x14] = *flags, area
    struct.pack_into('<4I', data, 4, control, size, view, 0x8C3)
    struct.pack_into('<2I', data, 0x18, ratio, result)
    c.u.mem_write(arg, bytes(data))
    c.u.mem_write(control, struct.pack('<6H', *control_wh, 0, 0, *control_wh) + bytes(12))
    c.u.mem_write(size, struct.pack('<2H', *viewport))
    c.u.mem_write(view, struct.pack('<4H', 0, 0, *viewport))
    c.u.mem_write(ratio, struct.pack('<2I', 1, 1))
    c.u.mem_write(result, b'\xA5' * 8)
    c.call(0x1018ADF0, (arg,))
    return list(struct.unpack('<4h', c.u.mem_read(result, 8)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--camera-before', type=Path)
    p.add_argument('--camera-after', type=Path)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    from verify_aspect_capture_branch import run
    stock = extract_eg(a.source.read_bytes())
    image, previous = a.image.read_bytes(), a.previous_image.read_bytes()
    report, old_report = [json.loads(p.read_text('utf-8')) for p in (a.report, a.previous_report)]
    native = fixture(stock, stock, None)
    wanted = fit(native)
    assert wanted == [0, 36, 720, 442]
    reproduced = []
    for profile, rect in ((1, [40, 0, 680, 480]), (2, [60, 0, 660, 480])):
        before = fit(fixture(stock, previous, old_report, profile))
        after = fit(fixture(stock, image, report, profile))
        previous_fixed = '0x1018ae57' in old_report['features']['aspect_profiles'].get('liveview_native_output_callers', [])
        assert before == (wanted if previous_fixed else rect) and after == wanted
        reproduced.append(dict(profile=profile, previous_rect=before, new_rect=after))
    cases, baseline = [], {}
    for profile in (0, 1, 2, 3, 6, 7):
        for user_area in (0, 2, 4):
            current = fixture(stock, image, report, profile, user_area)
            old = fixture(stock, previous, old_report, profile, user_area)
            for area in (0, 2, 4):
                for viewport in ((720, 480), (1920, 1080), (480, 720), (5568, 3712)):
                    for flags in ((0, 0), (1, 0), (0, 1)):
                        args = dict(area=area, viewport=viewport, control_wh=viewport, flags=flags)
                        actual = fit(current, **args)
                        key = (area, viewport, flags)
                        if key not in baseline:
                            baseline[key] = fit(native, **args)
                        expected = fit(old, **args) if profile in (3, 6) else baseline[key]
                        assert actual == expected, (profile, user_area, args, actual, expected)
                        cases.append(dict(profile=profile, user_area=user_area, **args, rect=actual))
    captures = []
    for profile in range(7):
        for size in range(3):
            for branch in (0, 3):
                for mode in (0, 2):
                    args = dict(profile=profile, size=size, branch=branch, mode=mode)
                    actual = run(stock, image, report, **args)
                    assert actual == run(stock, previous, old_report, **args)
                    captures.append(actual)
    camera = None
    if a.camera_before and a.camera_after:
        from read_aspect_trace import decode
        before, after = [decode(p.read_bytes()) for p in (a.camera_before, a.camera_after)]
        matching = lambda log, profile: [r for r in log['records'] if r['caller'] == '0x1018ae57'
                                       and r['profile'] == profile and r.get('area') == 2]
        first, reboot = matching(before, 1), matching(after, 0)
        assert first and reboot
        assert all(r['wh'] == [4952, 3712] for r in first)
        assert all(r['wh'] == [5568, 3128] for r in reboot)
        camera = dict(first_query_profile=1, first_query_wh=[4952, 3712],
                      reboot_query_profile=0, reboot_query_wh=[5568, 3128],
                      limitation='Logs confirm query order/dimensions; full caller arguments are synthetic.')
    result = dict(image_sha256=sha256(image), previous_sha256=sha256(previous),
                  reproduced_display_fit_cases=reproduced, native_function_cases=cases,
                  v15_capture_equivalence_cases=captures, camera_query_evidence=camera,
                  camera_fix_verified=False,
                  limits=['Whole 1018ADF0 executes, including native ratio classification and rectangle primitives.',
                          'Control blocks, viewports and branch flags are explicitly synthetic.',
                          'No direct write of a hard-coded 720x480 cache, sleep, wake or MMIO operation.',
                          'Reference-frame alignment and native-grid coexistence remain unresolved.'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'PASS {len(cases)} native display-fit cases, {len(captures)} capture comparisons, camera query evidence={camera is not None}')


if __name__ == '__main__':
    main()
