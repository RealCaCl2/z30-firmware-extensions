"""Replay the camera-observed branch through native crop/scale preparation.

The v10 camera trace selected info+0x228 == 3 and processing mode 0. This
fixture reproduces those scalar conditions; it is not a sensor/ISP model.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def run(stock, image, report, profile=1, size=0, branch=3, mode=0, area=2, user_area=2):
    from native_runtime import Context, RAM
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
    if report is None:
        c = Context(stock, image)
    else:
        c = Fixture(stock, image, report, {}, properties={0x501: user_area})
        state = c.state + report['features']['aspect_profiles']['state_offset']
        c.u.mem_write(state, struct.pack('<I', profile))
    u = c.u
    root, ctx, info, cfg, req, params, scratch, holder = [RAM+x for x in
        (0x20000, 0x30000, 0x40000, 0x50000, 0x51000, 0x60000, 0x70000, 0x80000)]
    def word(a, value):
        u.mem_write(a, struct.pack('<I', value))
    def ret(value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    word(cfg+4, holder)
    word(holder, holder+0x100)
    word(info+0x208, area)
    word(info+0x228, branch)
    u.mem_write(info+0x229, bytes([size]))
    c.call(0x1014237A, (cfg, info, 0))
    config = bytes(u.mem_read(cfg, 0x70))
    word(root+0xC, ctx)
    word(ctx+0x30C, cfg)
    word(ctx+0x3A4, info)
    blobs = []
    def boundary(u, address, length, unused):
        if address == 0x100EF7B8:
            blobs.append(bytes(u.mem_read(u.reg_read(UC_ARM_REG_R0), 72)))
        ret()
    for a in (0x100A11CA, 0x100EF7B8):
        u.hook_add(UC_HOOK_CODE, boundary, begin=a, end=a)
    c.call(0x100A4E20, (root,))
    assert len(blobs) == 1
    u.mem_write(req, blobs[0])
    word(req+8, scratch)
    # mode 0 was observed in the translated camera record. The guard at
    # info+0x33C was not logged, so this byte is an explicit model input.
    u.mem_write(info+0x33C, bytes([1 if mode == 0 else 0]))
    c.call(0x100EF6DE, (req,))
    assert u.mem_read(scratch+0xC, 1)[0] == mode
    c.call(0x102507CA, (scratch, params))
    parameters = bytes(u.mem_read(params, 0x500))
    word(scratch+0x4758, RAM+0x90000)
    packer = 0x1024D9FC if mode == 0 else 0x1024B7A8
    packed = []
    def pack(u, address, length, unused):
        packed.append(address)
        ret(1)
    u.hook_add(UC_HOOK_CODE, pack, begin=packer, end=packer)
    c.call(0x1024E2D6 if mode == 0 else 0x1024C084, (scratch, params))
    assert packed == [packer]
    return dict(profile=profile, size=size, branch228=branch, mode=mode,
                area=area, user_area=user_area,
                parent_wh=list(struct.unpack_from('<2H', config, 0x22)),
                trim_xy=list(struct.unpack_from('<2H', config, 0x38)),
                trimmed_wh=list(struct.unpack_from('<2H', config, 0x3C)),
                output_wh=list(struct.unpack_from('<2H', config, 0x56)),
                request_margins=list(struct.unpack_from('<4I', blobs[0], 0x10)),
                parameter_margins=list(struct.unpack_from('<4I', parameters, 0x3B0)),
                effective_input_wh=list(struct.unpack('<2I', u.mem_read(params+0x3A8, 8))),
                scale_q16=list(struct.unpack('<2I', u.mem_read(params+0x438, 8))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--camera-trace', type=Path, help='Optional decoded v10 JSON, retained locally')
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    stock = extract_eg(a.source.read_bytes())
    image, old = a.image.read_bytes(), a.previous_image.read_bytes()
    report = json.loads(a.report.read_text('utf-8'))
    previous = json.loads(a.previous_report.read_text('utf-8'))
    before = run(stock, old, previous)
    assert before['trim_xy'] == [0, 0] and before['output_wh'] == [4952, 3712]
    assert before['request_margins'] == before['parameter_margins'] == [0, 0, 0, 0]
    assert before['effective_input_wh'] == [5568, 3712] and before['scale_q16'][0] > 65536
    trace_match = None
    if a.camera_trace:
        trace = json.loads(a.camera_trace.read_text('utf-8'))
        assert trace['version'] == 3
        groups = {event: [r for r in trace['records'] if r['profile'] == 1 and r['event'] == event]
                  for event in (2, 3, 4)}
        assert all(groups.values())
        config, request, parameters = [groups[event][-1] for event in (2, 3, 4)]
        assert config['capture']['word228'] == 3
        assert config['trim_xy'] == before['trim_xy']
        assert request['margins_lrtb'] == before['request_margins']
        assert parameters['margins_lrtb'] == before['parameter_margins']
        assert parameters['processing_mode'] == before['mode']
        assert request['source_wh'] == parameters['source_wh'] == before['parent_wh']
        assert config['output_wh'] == request['output_wh'] == parameters['output_wh'] == before['output_wh']
        trace_match = dict(matched=True, profile=1,
                           sequences=[config['sequence'], request['sequence'], parameters['sequence']],
                           limitation='Latest per-stage snapshots; no shared capture ID. Scale values are computed, not logged.')
    targets, guards = [], []
    for profile, trim, width in ((1, 308, 4952), (2, 464, 4640)):
        for size in range(3):
            for branch in (0, 1, 3):
                for mode in (0, 2):
                    case = run(stock, image, report, profile, size, branch, mode)
                    assert case['trim_xy'][0] == trim
                    assert case['trimmed_wh'][0] == width
                    assert case['request_margins'][:2] == [trim, trim]
                    assert case['parameter_margins'] == case['request_margins']
                    assert case['effective_input_wh'][0] == width
                    if size == 0:
                        assert case['output_wh'][0] == width and case['scale_q16'] == [65536, 65536]
                    targets.append(case)
    # OFF and a user area other than 2 must return native geometry, even if
    # capture info still says area 2. Wide-profile geometry must stay unchanged.
    for profile, user_area, area in ((0, 2, 2), (1, 0, 2), (2, 4, 2),
                                     (1, 2, 0), (2, 2, 4), (6, 2, 2)):
        for branch in (0, 3):
            for mode in (0, 2):
                case = run(stock, image, report, profile=profile, branch=branch,
                           mode=mode, area=area, user_area=user_area)
                if profile == 6:
                    expected = run(stock, old, previous, profile=profile, branch=branch,
                                   mode=mode, area=area, user_area=user_area)
                else:
                    expected = run(stock, stock, None, profile=profile, branch=branch,
                                   mode=mode, area=area, user_area=user_area)
                assert case == expected
                guards.append(case)
    result = dict(image_sha256=sha256(image), previous_sha256=sha256(old),
                  reproduced_v10_failure=before, target_cases=targets, guard_cases=guards,
                  camera_trace_scalar_match=trace_match,
                  passed_cases=len(targets)+len(guards), camera_verified=False,
                  limitations=['Scalar branch values come from the v10 user trace; this is synthetic replay.',
                               'Request preparation 100A11CA is stubbed; scratch storage is substituted.',
                               'Mode 0 is selected with a modeled info+0x33C guard, not a logged guard value.',
                               'Tile packers are stubbed; no pixel, DMA, sensor or ISP execution proof.'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(f'PASS reproduced v10 failure and {result["passed_cases"]} target/guard cases')


if __name__ == '__main__':
    main()
