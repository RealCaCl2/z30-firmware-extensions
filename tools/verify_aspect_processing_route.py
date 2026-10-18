"""Execute stock crop consumers through request, parameter and scale generation.

Stops at the tile packer: no image bytes, hardware or physical capture proof.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def measure(stock, image, area, size):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
    c = Context(stock, image)
    u = c.u
    root, ctx, info, cfg = [RAM + x for x in (0x10000, 0x20000, 0x30000, 0x40000)]
    request, params, scratch, holder = [RAM + x for x in (0x41000, 0x50000, 0x60000, 0x70000)]
    def word(address, value):
        u.mem_write(address, struct.pack('<I', value))
    def ret(value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    word(cfg + 4, holder)
    word(holder, holder + 0x100)
    u.mem_write(info + 0x208, bytes([area]))
    u.mem_write(info + 0x229, bytes([size]))
    c.call(0x1014237A, (cfg, info, 0))
    word(root + 0xC, ctx)
    word(ctx + 0x30C, cfg)
    word(ctx + 0x3A4, info)
    requests = []
    def boundary(u, address, length, unused):
        if address == 0x100EF7B8:
            requests.append(bytes(u.mem_read(u.reg_read(UC_ARM_REG_R0), 0x48)))
        ret()
    for address in (0x100A11CA, 0x100EF7B8):
        u.hook_add(UC_HOOK_CODE, boundary, begin=address, end=address)
    c.call(0x100A4E20, (root,))
    assert len(requests) == 1
    raw = requests[0]
    u.mem_write(request, raw)
    # Replace the global destination with an explicitly owned scratch object.
    word(request + 8, scratch)
    c.call(0x100EF6DE, (request,))
    c.call(0x102507CA, (scratch, params))
    before = bytes(u.mem_read(params, 0x500))
    packer_calls = []
    def packer(u, address, length, unused):
        packer_calls.append(address)
        ret(1)
    u.hook_add(UC_HOOK_CODE, packer, begin=0x1024B7A8, end=0x1024B7A8)
    word(scratch + 0x4758, RAM + 0x80000)
    c.call(0x1024C084, (scratch, params))
    assert packer_calls == [0x1024B7A8]
    return dict(area=area, size=size,
                request_trims=list(struct.unpack_from('<4I', raw, 0x10)),
                request_source_output_wh=list(struct.unpack_from('<4H', raw, 0x20)),
                parameter_trims=list(struct.unpack_from('<4I', before, 0x3B0)),
                effective_input_wh=list(struct.unpack('<2I', u.mem_read(params + 0x3A8, 8))),
                scale_q16=list(struct.unpack('<2I', u.mem_read(params + 0x438, 8))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT / 'Z_30_0120.bin')
    p.add_argument('--deps', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    sys.path.insert(0, str(args.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    from build_dx_aspect_trials import build_trial
    stock = extract_eg(args.source.read_bytes())
    cases = []
    for label, image in [('stock', stock), ('4-3', build_trial(stock, (4, 3))[0]),
                         ('5-4', build_trial(stock, (5, 4))[0])]:
        for area in (0, 2, 4):
            for size in range(3):
                result = measure(stock, image, area, size)
                left, right, top, bottom = result['request_trims']
                sw, sh, ow, oh = result['request_source_output_wh']
                assert result['parameter_trims'] == [left, right, top, bottom]
                assert result['effective_input_wh'] == [sw - left - right, sh - top - bottom]
                if size == 0:
                    assert result['effective_input_wh'] == [ow, oh]
                    assert result['scale_q16'] == [65536, 65536]
                result.update(image=label, image_sha256=sha256(image))
                cases.append(result)
    report = dict(passed_cases=len(cases), cases=cases, camera_verified=False,
                  scope='Normal synthetic configuration -> native request -> native parameters -> native mode-2 scale calculation',
                  boundaries=['Capture context and processing inputs are synthetic.',
                              '100A11CA preparation call is stubbed.',
                              '100EF7B8 is intercepted; 100EF6DE and 102507CA run separately on the captured request.',
                              'Only the mode-2 plan is executed, with tile packer 1024B7A8 stubbed.',
                              'No command stream, pixels, ISP execution or real capture selection proof.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'PASS {len(cases)} processing-route cases; physical crop remains unverified')


if __name__ == '__main__':
    main()
