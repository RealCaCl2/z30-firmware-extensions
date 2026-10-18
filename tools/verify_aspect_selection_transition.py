"""Exercise geometry reads inside the native-area setter notification boundary.

The notification is an explicit reentrant fixture, not the camera scheduler.
Native generated menu/selector/save routines execute with checked ABI.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def run(stock, image, report, old_profile, selected, area, previous_area=4, fault=None):
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC, UC_ARM_REG_LR
    from z30_extensions.assembly import assemble, invoke, constant
    c = Fixture(stock, image, report, {})
    feature = report['features']['aspect_profiles']
    f = {k: int(v, 16) for k, v in feature['functions'].items()}
    state = c.state + feature['state_offset']
    c.u.mem_write(state, struct.pack('<4I', old_profile, 0xFFFFFFFF, previous_area, 0))
    page, proxy, probe = 0x50080000, 0x50090000, 0x50091000
    c.u.mem_write(page, bytes(44))
    c.u.mem_write(page + 22, struct.pack('<H', selected))
    notifications, props = [], {0x501: area}
    code = f'''push {{r4,lr}};{constant('r4',probe)}
        movs r0,#2;movs r1,#0;{invoke(0x1020BFDA)}ldr r0,[r0];str r0,[r4];
        movs r0,#0;movs r1,#2;movs r2,#0;{invoke(0x1020BFC8)}ldr r0,[r0];str r0,[r4,#4];
        movs r0,#1;pop {{r4,pc}};'''
    c.u.mem_write(proxy, assemble(code, proxy, 128))

    def api(u, address, length, unused):
        key, value = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)
        if address == 0x10465990:
            assert key == 0x501
            profile = struct.unpack('<I', u.mem_read(state, 4))[0]
            notifications.append(dict(profile_at_setter=profile, old_area=props[key], requested_area=value))
            if fault == 'reject':
                c.ret(0)
            elif fault == 'readback':
                c.ret(1)  # Simulate success status but unchanged property.
            else:
                props[key] = value
                u.reg_write(UC_ARM_REG_PC, proxy | 1)
            return
        if address == 0x10465984:
            result = props.get(key, 0)
        elif address == 0x101993DE:
            result = int(fault == 'busy') if key == 0x188C else 0
        else:
            result = 0  # Native page switch only; no UI model here.
        c.ret(result)

    for address in (0x10465984, 0x10465990, 0x101993DE, 0x1045F732):
        c.u.hook_add(UC_HOOK_CODE, api, begin=address, end=address)
    c.call(f['aspect_action'], (page,))
    final = struct.unpack('<4I', c.u.mem_read(state, 16))
    output = c.call(0x1020BFDA, (2, 0))
    internal = c.call(0x1020BFC8, (0, 2, 0))
    after = bytes(c.u.mem_read(output, 4)) + bytes(c.u.mem_read(internal, 4))
    observed = bytes(c.u.mem_read(probe, 8))
    return dict(old_profile=old_profile, selected=selected, starting_area=area,
                previous_area=previous_area, fault=fault, notifications=notifications,
                during_geometry_hex=observed.hex(), after_geometry_hex=after.hex(),
                final_profile=final[0], final_previous_area=final[2], final_area=props[0x501],
                saved=bool(c.files), during_matches_after=observed == after)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    stock = extract_eg(a.source.read_bytes())
    image, previous = a.image.read_bytes(), a.previous_image.read_bytes()
    report, old_report = [json.loads(p.read_text('utf-8')) for p in (a.report, a.previous_report)]
    before, cases, guards = [], [], []
    for profile in (1, 2):
        old = run(stock, previous, old_report, 0, profile, 0)
        assert old['notifications'][0]['profile_at_setter'] == 0
        assert not old['during_matches_after']
        before.append(old)
    for old_profile in range(7):
        for selected in range(7):
            for area in (0, 2, 4):
                case = run(stock, image, report, old_profile, selected, area)
                assert case['final_profile'] == selected
                if not old_profile and not selected:
                    assert not case['notifications'] and not case['saved']
                    assert case['final_area'] == area
                else:
                    assert case['notifications'][0]['profile_at_setter'] == selected
                    assert case['during_matches_after'] and case['saved']
                    assert case['final_area'] == (2 if selected else 4)
                    assert case['final_previous_area'] == (area if not old_profile else 4)
                cases.append(case)
    for old_profile, selected, area in ((0, 1, 0), (0, 2, 4), (1, 2, 0), (2, 0, 2)):
        for fault in ('reject', 'readback', 'busy'):
            case = run(stock, image, report, old_profile, selected, area, fault=fault)
            assert case['final_profile'] == old_profile and case['final_previous_area'] == 4
            assert not case['saved'] and case['final_area'] == area
            guards.append(case)
    invalid = run(stock, image, report, 1, 7, 2)
    assert invalid['final_profile'] == 1 and not invalid['notifications'] and not invalid['saved']
    guards.append(invalid)
    result = dict(image_sha256=sha256(image), previous_sha256=sha256(previous),
                  reproduced_v13_ordering=before, transition_cases=cases, failure_guard_cases=guards,
                  camera_verified=False,
                  limits=['Property setter notification is a reentrant fixture, not native task scheduling.',
                          'Geometry is queried inside setter after property update; sensor/LCD pixels are absent.',
                          'Native setter side effects cannot be undone by rolling back extension selection.',
                          'Real first-enable and reboot behavior require camera retesting.'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'PASS {len(cases)} reentrant transitions, {len(guards)} failure/guard cases, {len(before)} v13 ordering gaps')


if __name__ == '__main__':
    main()
