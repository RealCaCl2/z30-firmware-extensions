"""QEMU guest execution of native highlight derivation with diagnostic inputs.

No artificial zebra pixels are drawn. This does not validate the sensor or
highlights display consumer. Uses the existing assisted native-task fixture.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def prepare(image, path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from z30_extensions.assembly import assemble, invoke
    address = (0x10020000 + len(image) + 3) & ~3
    code = assemble(f'''
        push {{r4, lr}}; mov r4, r0;
        add.w r3, r4, #0xa0;
        movs r0, #8; str r0, [r3];
        movs r0, #0; str r0, [r3, #4];
        add.w r0, r4, #0x160; str r0, [r3, #8];
        add.w r0, r4, #0x164; str r0, [r3, #12];
        add.w r0, r4, #0x88; str.w r0, [r3, #0x15c];
        movs r1, #1; str r1, [r0];
        add.w r1, r4, #0x90; str r1, [r0, #4];
        movw r0, #0x189a; str r0, [r1];
        movs r0, #0; str r0, [r1, #4];
        movw r0, #0x8f8c; movt r0, #0x114e;
        mov r1, r4;
        {invoke(0x101776FE)}
        pop {{r4, pc}};
    ''', address, 256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(address=hex(address), code=code.hex(),
                                   candidate_sha256=hashlib.sha256(image).hexdigest()), indent=2)+'\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--report', type=Path)
    p.add_argument('--prepare-fixture', type=Path)
    p.add_argument('--fixture', type=Path)
    p.add_argument('--qemu-root', type=Path)
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--natural-check', action='store_true',
                   help='Also inspect native property notifications in the assisted live-view session')
    p.add_argument('--derive-service', action='store_true',
                   help='Explicitly run the native two-phase derivation service after diagnostic settings')
    p.add_argument('--transition-check', action='store_true',
                   help='After derivation initialization, test pattern/threshold updates')
    p.add_argument('--transition-phase', type=int, choices=(0, 1), default=1,
                   help='Derivation phase for transitions; phase 1 enumerates the whole native registry')
    p.add_argument('--submit-request', action='store_true',
                   help='Explicitly invoke the native request builder after derived setting changes')
    p.add_argument('--stock-video-control', action='store_true',
                   help='Compare the parent GUI update with restored stock mode gates in video mode')
    args = p.parse_args()
    if (args.derive_service or args.transition_check or args.submit_request) and not args.natural_check:
        p.error('--derive-service and --transition-check require --natural-check')
    if args.transition_check and not args.derive_service:
        p.error('--transition-check requires --derive-service')
    if args.submit_request and not args.derive_service:
        p.error('--submit-request requires --derive-service')
    if args.stock_video_control and not args.derive_service:
        p.error('--stock-video-control requires --derive-service')
    image = args.image.read_bytes()
    if args.prepare_fixture:
        prepare(image, args.prepare_fixture)
        return
    if not all((args.report, args.fixture, args.qemu_root, args.output_dir)):
        p.error('Guest execution requires --report, --fixture, --qemu-root and --output-dir')
    fixture = json.loads(args.fixture.read_text())
    report = json.loads(args.report.read_text())
    digest = hashlib.sha256(image).hexdigest()
    if digest != fixture['candidate_sha256'] or digest != report['sha256']:
        raise ValueError('Candidate image/fixture/report identity mismatch')
    sys.path.insert(0, str(args.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import DEVICE_ENV, start
    s = Session(str(args.output_dir.resolve()), device_env=DEVICE_ENV)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            block = data[offset:offset+1024]
            if s.remote.command(f'M{address+offset:x},{len(block):x}:' + block.hex()) != 'OK':
                raise RuntimeError('Guest memory write failed')
        if s.remote.memory(address, len(data)) != data:
            raise RuntimeError('Guest memory readback failed')

    cases = [('photo-pattern1', 0, 1, 3, 29, 0, 0),
             ('photo-pattern2', 0, 2, 3, 29, 0, 0),
             ('photo-off', 0, 0, 3, 29, 0, 0),
             ('photo-state-block', 0, 1, 3, 29, 0x20, 0),
             ('photo-scene-block', 0, 1, 3, 0, 0, 0),
             ('photo-suppress', 0, 1, 3, 29, 0, 1),
             ('video-pattern1', 1, 1, 3, 29, 0, 0)]
    observed = {}
    try:
        write(int(fixture['address'], 16), bytes.fromhex(fixture['code']))
        start(s)
        for phase in (('stock', 'candidate-off', 'candidate') if 'photo_zebra_control' in report['features'] else ('stock', 'candidate')):
            if phase == ('candidate-off' if 'photo_zebra_control' in report['features'] else 'candidate'):
                for edit in report['edits']:
                    address = 0x10020000 + int(edit['offset'], 16)
                    if s.remote.memory(address, len(bytes.fromhex(edit['expected_hex']))) != bytes.fromhex(edit['expected_hex']):
                        raise RuntimeError('Guest stock instruction mismatch')
                    write(address, bytes.fromhex(edit['replacement_hex']))
                stock_size = (args.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
                write(0x10020000 + stock_size - 2, image[stock_size-2:])
            if phase != 'stock' and 'photo_zebra_control' in report['features']:
                # Explicit menu-state fixture; real navigation/SD is verified
                # separately by verify_photo_zebra_menu_qemu.py.
                state = 0xC6001000
                write(0xC1832D34, struct.pack('<I',state))
                write(state+56, struct.pack('<I',0x5A33534E))
                write(state+report['features']['photo_zebra_control']['state_offset'],
                      struct.pack('<I',int(phase=='candidate')))
            for name, movie, pattern, peaking, scene, flags, suppress in cases:
                fields = [0]*34
                fields[1], fields[3], fields[4], fields[5], fields[6] = pattern, movie, peaking, scene, flags
                fields[16], fields[27] = movie, suppress
                payload = struct.pack('<34I', *fields) + bytes(0x200-34*4)
                result = s.input(int(fixture['address'], 16), [('scratch', 0)], payload=payload)
                if not result['completed'] or not result['context_restored'] or not result['scratch']['restored']:
                    raise RuntimeError('Guest diagnostic callback did not complete/restore')
                returned = bytes.fromhex(result['scratch']['result'])
                count, = struct.unpack_from('<I', returned, 0xa4)
                key, = struct.unpack_from('<I', returned, 0x160)
                value, = struct.unpack_from('<I', returned, 0x164)
                dirty, = struct.unpack_from('<I', returned, 0x94)
                expected = pattern if (movie or phase == 'candidate') and not (flags or suppress) and scene == 29 else 0
                if (count, key, value, dirty) != (1, 0x189A, expected, 1):
                    raise AssertionError((phase, name, count, hex(key), value, dirty, expected))
                observed[f'{phase}/{name}'] = value
                print(phase, name, value, flush=True)
        s.audit.append(dict(kind='photo-zebra-native-derivation', candidate_sha256=digest,
                            results=observed, diagnostic_snapshots=True,
                            realtime_zebra_pixels_verified=False, camera_verified=False))
        s.write_audit()
        if args.natural_check:
            settings = ((4, 0), (0x188C, 0), (2, 29), (0x112A, 3), (0x1183, 1), (0x1182, 1))
            events = []
            for key, value in settings:
                result = s.input(0x10465990, [key, value],
                                 observe=(0x101776FE, 0x1017784A, 0x1044F398, 0x1045580C))
                events.append(dict(key=hex(key), requested=value, result=result))
                if not result['completed'] or not result['context_restored']:
                    break
                s.run_for(.5)
            if args.derive_service:
                def capture(remote, registers):
                    pc = int(registers['r15'], 16)
                    if pc in (0x101776FE, 0x1017784A):
                        pointer = int(registers['r1'], 16)
                        return dict(dependency_snapshot=remote.memory(pointer, 136).hex())
                    if pc == 0x1045580C:
                        pointer = int(registers['r2'], 16)
                        return dict(route=int(registers['r0'], 16), request=remote.memory(pointer, 36).hex())
                    if pc == 0x1044F398:
                        return dict(action=int(registers['r0'], 16))
                    return {}
                def submit(expected_pattern, expected_threshold):
                    result = s.input(0x1044F398, [2], seconds=10,
                                     observe=(0x1045580C, 0x104B6FCC, 0x103C57D0,
                                              0x103C12A2, 0x103C033A), capture=capture)
                    requests = [item for item in result.get('observations', []) if 'request' in item]
                    if [item['route'] for item in requests] != [1, 4]:
                        raise AssertionError('Native output requests were not submitted on both active routes')
                    for item in requests:
                        data = bytes.fromhex(item['request'])
                        enabled, = struct.unpack_from('<I', data)
                        threshold, = struct.unpack_from('<H', data, 8)
                        if (enabled != int(bool(expected_pattern)) or threshold != expected_threshold
                                or data[32] != {0: 2, 1: 0, 2: 1}[expected_pattern]):
                            raise AssertionError('Native output request differs from the derived setting')
                    record = dict(kind='explicit-native-display-request', result=result,
                                expected_pattern=expected_pattern, expected_threshold=expected_threshold,
                                consumer_reached=any(item.get('pc') == '0x103c12a2'
                                                     for item in result.get('observations', [])),
                                dispatch_reached=any(item.get('pc') == '0x103c033a'
                                                     for item in result.get('observations', [])))
                    if not record['consumer_reached'] or not record['dispatch_reached']:
                        raise AssertionError('Native request did not reach the display consumer and command dispatch')
                    return record
                for phase in (0, 1):
                    result = s.input(0x1018FE4A, [('scratch', 0)],
                                     payload=struct.pack('<IB3x', 0xC18A72A0, phase),
                                     observe=(0x101776FE, 0x1017784A, 0x1044F398, 0x1045580C),
                                     capture=capture)
                    events.append(dict(kind='explicit-derivation-phase', phase=phase, result=result))
                    s.run_for(.5)
                result = s.input(0x10368310, [0, 1],
                                 observe=(0x1044F398, 0x1045580C), capture=capture)
                events.append(dict(kind='native-gui-refresh', result=result,
                                   latch=s.remote.memory(0xC1821E60, 4).hex()))
                if args.submit_request:
                    events.append(submit(1, 248))
                if args.transition_check:
                    for key, value, expected in ((0x1182, 2, 2), (0x1183, 7, 2),
                                                 (0x1182, 0, 0), (0x1182, 1, 1)):
                        setter = s.input(0x10465990, [key, value])
                        derived = s.input(0x1018FE4A, [('scratch', 0)],
                                          payload=struct.pack('<IB3x', 0xC18A72A0, args.transition_phase),
                                          observe=(0x101776FE, 0x1017784A, 0x1044F398, 0x1045580C),
                                          capture=capture)
                        refreshed = s.input(0x10368310, [0, 1],
                                            observe=(0x1044F398, 0x1045580C), capture=capture)
                        actual = s.input(0x10465984, [0x189A])
                        observed_value = int(actual['registers']['r0'], 16)
                        events.append(dict(kind='native-setting-transition', key=hex(key), value=value,
                                           phase=args.transition_phase,
                                           expected_effective=expected, observed_effective=observed_value,
                                           setter=setter, derivation=derived, refresh=refreshed))
                        s.audit.append(events[-1])
                        s.write_audit()
                        print('transition', hex(key), value, 'effective', observed_value, flush=True)
                        if observed_value != expected:
                            raise AssertionError('Native setting transition did not update the effective pattern')
                        if args.submit_request:
                            events.append(submit(expected, 180 if key == 0x1183 or value in (0, 1) else 248))
            getters = {}
            for key in (4, 0x188C, 2, 0x112A, 0x1182, 0x1183, 0x189A):
                result = s.input(0x10465984, [key])
                getters[hex(key)] = result
                print('natural-get', hex(key), result.get('registers', {}).get('r0'), flush=True)
                if not result['completed'] or not result['context_restored']:
                    break
            if args.stock_video_control:
                for edit in report['edits']:
                    write(0x10020000 + int(edit['offset'], 16), bytes.fromhex(edit['expected_hex']))
                control = dict(kind='stock-video-parent-update-control', mode_gates_restored=True)
                control['setters'] = [s.input(0x10465990, [key, value])
                                      for key, value in ((4, 1), (0x188C, 1), (0x1182, 1))]
                control['derivation'] = s.input(0x1018FE4A, [('scratch', 0)],
                    payload=struct.pack('<IB3x', 0xC18A72A0, 1),
                    observe=(0x101776FE, 0x1017784A, 0x1044F398, 0x1045580C), capture=capture)
                control['parent_update'] = s.input(0x10368310, [0, 1],
                    observe=(0x1044F398, 0x1045580C), capture=capture)
                control['gui_scene'] = s.remote.memory(0xC18292AC, 1).hex()
                control['effective'] = s.input(0x10465984, [0x189A])
                s.audit.append(control)
                s.write_audit()
                print('stock-video-control', control['effective']['registers']['r0'],
                      'parent hits', len(control['parent_update'].get('observations', [])), flush=True)
                for edit in report['edits']:
                    write(0x10020000 + int(edit['offset'], 16), bytes.fromhex(edit['replacement_hex']))
                photo = dict(kind='candidate-return-to-photo-control')
                photo['setters'] = [s.input(0x10465990, [key, value])
                                    for key, value in ((4, 0), (0x188C, 0), (0x1183, 1), (0x1182, 2))]
                photo['derivation'] = s.input(0x1018FE4A, [('scratch', 0)],
                    payload=struct.pack('<IB3x', 0xC18A72A0, 1),
                    observe=(0x101776FE, 0x1017784A, 0x1044F398, 0x1045580C), capture=capture)
                photo['parent_update'] = s.input(0x10368310, [0, 1],
                    observe=(0x102F6F2E, 0x102F6F34, 0x1044F398, 0x1045580C), capture=capture)
                photo['gui_scene'] = s.remote.memory(0xC18292AC, 1).hex()
                photo['effective'] = s.input(0x10465984, [0x189A])
                s.run_for(.3)
                photo['frame'] = str(s.frame('photo-pattern2-threshold1'))
                s.audit.append(photo)
                s.write_audit()
                print('return-to-photo', photo['effective']['registers']['r0'],
                      'gui scene', photo['gui_scene'],
                      'parent hits', len(photo['parent_update'].get('observations', [])), flush=True)
                pattern, threshold = 2, 248
                automatic_points = (0x1044F398, 0x1045580C, 0x103C12A2, 0x103C033A)
                for key, value in ((0x1183, 7), (0x1182, 0), (0x1182, 1), (0x1183, 0)):
                    audit_start = len(s.audit)
                    update = dict(kind='candidate-photo-parent-setting-update', key=hex(key), value=value)
                    update['setter'] = s.input(0x10465990, [key, value],
                                              observe=automatic_points, capture=capture)
                    update['derivation'] = s.input(0x1018FE4A, [('scratch', 0)],
                        payload=struct.pack('<IB3x', 0xC18A72A0, 1),
                        observe=automatic_points, capture=capture)
                    update['parent_update'] = s.input(0x10368310, [0, 1],
                        observe=automatic_points, capture=capture)
                    if key == 0x1182:
                        pattern = value
                    else:
                        threshold = {7: 180, 0: 255}[value]
                    requests = [item for call in s.audit[audit_start:]
                                for item in call.get('observations', []) if 'request' in item]
                    matching = []
                    for item in requests:
                        data = bytes.fromhex(item['request'])
                        enabled, = struct.unpack_from('<I', data)
                        actual_threshold, = struct.unpack_from('<H', data, 8)
                        if (enabled, actual_threshold, data[32]) == (
                                int(bool(pattern)), threshold, {0: 2, 1: 0, 2: 1}[pattern]):
                            matching.append(item['route'])
                    update['matching_routes'] = sorted(set(matching))
                    update['expected_pattern'] = pattern
                    update['expected_threshold'] = threshold
                    s.run_for(.3)
                    update['frame'] = str(s.frame(f'photo-setting-{key:x}-{value}'))
                    s.audit.append(update)
                    s.write_audit()
                    print('photo-parent-update', hex(key), value, 'matching routes', update['matching_routes'], flush=True)
                    if update['matching_routes'] != [1, 4]:
                        raise AssertionError('Photo parent update did not submit the changed native request')
            s.audit.append(dict(kind='photo-zebra-assisted-notification-check', setters=events,
                                getters=getters, diagnostic_only=True,
                                natural_boot_verified=False, realtime_zebra_pixels_verified=False))
            s.write_audit()
    finally:
        s.close()


if __name__ == '__main__':
    main()
