"""Compare native view-window updates and retain the v11 capture contract.

This checks cached geometry, not camera sensor or LCD pixels.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
VIEW_STATE, VIEW_UPDATE = 0xC237C380, 0x10197D78


def fixture(stock, image, report, profile, user_area):
    from native_runtime import Context
    from verify_sound_settings import Fixture
    if report is None:
        c = Context(stock, stock)
    else:
        c = Fixture(stock, image, report, {}, properties={0x501: user_area})
        c.u.mem_write(c.state+report['features']['aspect_profiles']['state_offset'], struct.pack('<I', profile))
    c.map(VIEW_STATE, 256)
    return c


def update(context, area):
    argument = 0x500E0000
    context.u.mem_write(VIEW_STATE, b'\xA5' * 256)
    context.u.mem_write(argument, bytes([0, 0, area, 0]))
    context.call(VIEW_UPDATE, (argument,))
    data = bytes(context.u.mem_read(VIEW_STATE, 256))
    return data, list(struct.unpack_from('<4H', data, 0x52))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    from verify_aspect_capture_branch import run
    stock = extract_eg(a.source.read_bytes())
    image, previous = a.image.read_bytes(), a.previous_image.read_bytes()
    report = json.loads(a.report.read_text('utf-8'))
    old_report = json.loads(a.previous_report.read_text('utf-8'))
    baseline = {area: update(fixture(stock, stock, None, 0, 2), area) for area in (0, 2, 4)}
    before = update(fixture(stock, previous, old_report, 1, 2), 2)
    after = update(fixture(stock, image, report, 1, 2), 2)
    assert before[1] == [308, 0, 5260, 3712]
    assert after[0] == baseline[2][0]
    assert after[1] == [0, 292, 5568, 3420]
    cases = []
    for profile in (0, 1, 2, 3, 6, 7):
        for user_area in (0, 2, 4):
            for area in (0, 2, 4):
                actual = update(fixture(stock, image, report, profile, user_area), area)
                if profile in (3, 6):
                    expected = update(fixture(stock, previous, old_report, profile, user_area), area)
                else:
                    expected = baseline[area]
                assert actual[0] == expected[0], (profile, user_area, area)
                cases.append(dict(profile=profile, user_area=user_area, area=area, rect=actual[1]))
    # A repeated native update after profile changes must not install narrow
    # JPEG dimensions into the native window. No display-cache invalidation is
    # synthesized here: this executes the existing updater each time.
    c = fixture(stock, image, report, 1, 2)
    lifecycle = []
    for profile in (1, 2, 1, 0, 2, 0):
        c.u.mem_write(c.state+report['features']['aspect_profiles']['state_offset'], struct.pack('<I', profile))
        data, rect = update(c, 2)
        assert data == baseline[2][0]
        lifecycle.append(dict(profile=profile, rect=rect))
    captures = []
    for profile in (1, 2):
        for size in range(3):
            for mode in (0, 2):
                current = run(stock, image, report, profile=profile, size=size, mode=mode)
                original = run(stock, previous, old_report, profile=profile, size=size, mode=mode)
                assert current == original
                captures.append(current)
    result = dict(image_sha256=sha256(image), previous_sha256=sha256(previous),
                  previous_narrow_view_rect=before[1], native_view_rect=after[1],
                  native_window_cases=cases, repeated_updates=lifecycle,
                  v11_capture_equivalence_cases=captures, camera_verified=False,
                  limits=['View area and user properties are synthetic inputs.',
                          'This checker covers one cached-window updater; other display paths have separate checks.',
                          'Wide-profile behavior is compared to v11, not declared correct.',
                          'No live-frame pixels, real intermittent transition or framing-coverage proof.',
                          'Capture equivalence uses the bounded processing model with tile packers stubbed.'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(f'PASS {len(cases)} view-window cases, {len(lifecycle)} repeated updates, {len(captures)} v11 capture comparisons')


if __name__ == '__main__':
    main()
