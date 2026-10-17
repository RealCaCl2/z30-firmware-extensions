"""Native UI service checks with the local research Unicorn fixtures.

Property storage, DISP cycle, histogram processing, descriptor filtering and
horizon comparisons execute native instructions. Locks, observer completion,
histogram input and snapshot RAM are supplied by the explicit research fixture.
"""
import argparse
import itertools
import json
import struct
import sys
from pathlib import Path

from z30_extensions.build import build
from z30_extensions.container import extract_eg


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--research-tools', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.research_tools.resolve()))
    from verify_liveview_dual import Context, SNAPSHOT, PREVIOUS
    from verify_cfw_ui_contract import abi_call
    from emulate_config_defaults import seed_property
    source = extract_eg(a.source.read_bytes())
    candidate, report = build(source, 'liveview-dual')
    stock, patched = Context(source), Context(candidate)
    print('Native contexts initialized', flush=True)
    for key in (0x3D15, 0x3D16, 0x3D17, 0x3D18):
        seed_property(patched.u, key, patched.mapped)
    cycle = []
    for current, expected in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 4), (4, 0)):
        patched.setup(current)
        abi_call(patched.u, 0x10372528, ())
        observed = patched.u.mem_read(SNAPSHOT + 0x196, 1)[0]
        assert observed == expected, (current, expected, observed)
        if expected == 5:
            # Native property selection/getter, not a stubbed setting value.
            assert abi_call(patched.u, 0x10366E40, ()) == 5
        cycle.append(dict(current=current, next=observed))
        print(f'Native DISP cycle {current} -> {observed}', flush=True)
    eligibility = []
    for movie, apply, block, active in itertools.product(range(2), repeat=4):
        states = []
        for session, disp in ((stock, 2), (patched, 5)):
            session.setup(disp, movie, apply, block, active)
            available = abi_call(session.u, 0x1044FF70, ())
            abi_call(session.u, 0x1036E8A4, ())
            states.append(dict(available=available, **session.histogram_state()))
        assert states[0] == states[1], (movie, apply, block, active, states)
        eligibility.append(dict(movie=movie, apply=apply, block=block, active=active, result=states[1]))
    masks = 0
    print('Histogram eligibility passed', flush=True)
    for group, output, nibble in itertools.product(range(3), range(3), range(3)):
        for session, disp in ((stock, 0), (patched, 5)):
            session.setup(disp, active=int(nibble == 1))
            session.u.mem_write(0xC1821E29, bytes([output]))
            session.u.mem_write(SNAPSHOT + 0x19F, bytes([1 if nibble == 2 else 0]))
            session.u.mem_write(SNAPSHOT + 0x380, struct.pack('<H', group))
            if group == 2:
                session.u.mem_write(SNAPSHOT + 0x308, struct.pack('<I', 1))
        for widget in range(65):
            result = []
            stock.u.mem_write(SNAPSHOT + 0x196, bytes([2 if widget == 31 else 0]))
            for session in (stock, patched):
                descriptor = session.word(0x1073F830 + widget * 4)
                control = session.word(descriptor + 0x30)
                session.u.mem_write(control, b'\x01\x01\0')
                result.append(abi_call(session.u, 0x103257A6, (widget,)))
            assert result[0] == result[1], (group, output, nibble, widget, result)
            masks += 1
        print(f'Descriptor group {group}/{output}/{nibble}: {masks} comparisons', flush=True)
    fresh = []
    for scene in range(3):
        result = []
        for session, disp in ((stock, 2), (patched, 5)):
            session.setup(disp)
            session.bins = [0] * 256
            session.bins[20 + scene * 25] = 100 + scene * 10
            before = session.histogram_samples
            abi_call(session.u, 0x1037249E, ())
            abi_call(session.u, 0x1036E8A4, ())
            result.append(dict(new_samples=session.histogram_samples - before,
                               scale=session.word(0xC201685C + 0x34), state=session.histogram_state()))
        assert result[0] == result[1] and result[1]['new_samples'] == 1
        fresh.append(result[1])
    for changed in (False, True):
        patched.setup(5)
        patched.u.mem_write(0xC18292AC, b'\0')
        patched.u.mem_write(PREVIOUS, bytes(patched.u.mem_read(SNAPSHOT, 0x468)))
        patched.u.mem_write(0xC1828BB4, struct.pack('<3HBx', 10, 20, 30, 1))
        patched.u.mem_write(0xC1828BDC, struct.pack('<3HBx', 11 if changed else 10, 20, 30, 1))
        assert abi_call(patched.u, 0x1036F052, ()) == int(changed)
    result = dict(passed=True, sha256=report['sha256'], cycle=cycle,
                  native_property_reload=True, eligibility=eligibility,
                  descriptor_comparisons=masks, fresh_histograms=fresh,
                  horizon_changed_and_unchanged=True, camera_verified=False,
                  limitations=__doc__)
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('passed', 'sha256', 'descriptor_comparisons', 'native_property_reload')}, indent=2))


if __name__ == '__main__':
    main()
