"""Execute complete independent preview buffer producers, including stock/v3.

Synthetic capture records and the area property are explicit fixtures. No JPEG
encoder, sensor or physical playback is executed by this regression.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def verify(stock, image, report, previous, previous_report):
    from verify_sound_settings import Fixture
    from native_runtime import Context
    from unicorn import UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
    from z30_extensions.platform.z30_120 import BASE, sha256
    area = [2]

    def fixture(binary, metadata):
        s = Fixture(stock, binary, metadata, {})
        s.u.mem_protect(BASE, (len(binary)+4095)&~4095, UC_PROT_READ|UC_PROT_EXEC)
        def getter(u, address, size, data):
            assert u.reg_read(UC_ARM_REG_R0) == 0x501
            u.reg_write(UC_ARM_REG_R0, area[0])
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
        s.u.hook_add(UC_HOOK_CODE, getter, begin=0x10465984, end=0x10465984)
        return s

    current = fixture(image, report)
    old = fixture(previous, previous_report)
    original = Context(stock, stock)
    obj, info, holder = 0x50080000, 0x50081000, 0x50088000
    producers = ((0x10142D86, (0x1a, 0x3a)),
                 (0x1014376E, (0x22,)),
                 (0x101464C4, (0x1a, 0x36)))

    def run(s, address, index):
        s.u.mem_write(obj, bytes(0x100))
        s.u.mem_write(info, bytes(0x6000))
        s.u.mem_write(obj+4, struct.pack('<I', holder))
        s.u.mem_write(holder, struct.pack('<I', 0x50090000))
        for delta in (0x226, 0x4e8):
            s.u.mem_write(info+delta, bytes([index]))
        s.call(address, (obj, info))
        return bytes(s.u.mem_read(obj, 0x100))

    cases = []
    regression = []
    for address, offsets in producers:
        stock_cases = {index: run(original, address, index) for index in range(5)}
        # Reproduce why v3's getter tests were insufficient: the complete
        # independent producers still return stock shapes at profile XPAN.
        area[0] = 2
        old.u.mem_write(old.state+previous_report['features']['aspect_profiles']['state_offset'],
                        struct.pack('<I', 6))
        before = run(old, address, 1)
        assert before == stock_cases[1]
        for profile in range(8):
            current.u.mem_write(current.state+report['features']['aspect_profiles']['state_offset'],
                                struct.pack('<I', profile))
            for active_area in (0, 2, 4):
                area[0] = active_area
                for index in range(5):
                    after = run(current, address, index)
                    custom = 1 <= profile <= 6 and active_area == 2 and index == 1
                    pairs = [list(struct.unpack_from('<2H', after, offset)) for offset in offsets]
                    if custom:
                        wanted = report['features']['aspect_profiles']['profiles'][profile-1]['preview_wh']
                        if len(offsets) == 1:
                            wanted = [wanted[1]]
                        assert pairs == [list(wh) for wh in wanted], (hex(address), profile, pairs, wanted)
                        # Native row strides are retained for both planar buffers.
                        fields = (0x16, 0x18) if len(offsets) == 2 else ()
                        for offset in fields:
                            assert after[offset:offset+2] == stock_cases[index][offset:offset+2]
                        if address == 0x10142d86:
                            assert struct.unpack_from('<2H', after, 0x56) == (160, 120)
                    else:
                        assert after == stock_cases[index], (hex(address), profile, active_area, index)
                    cases.append({'producer': hex(address), 'profile': profile,
                                  'area': active_area, 'index': index, 'wh': pairs, 'custom': custom})
                    if profile == 6 and active_area == 2 and index == 1:
                        regression.append({'producer': hex(address),
                            'v3_wh': [list(struct.unpack_from('<2H', before, offset)) for offset in offsets],
                            'v4_wh': pairs})
    return {'source_sha256': sha256(stock), 'image_sha256': sha256(image),
            'previous_image_sha256': sha256(previous), 'v3_regression': regression,
            'producer_cases': cases, 'rom_read_only': True,
            'models': ['Synthetic capture records, memory and user-area getter.',
                       'Complete native buffer producers, memset and arithmetic execute ARM instructions.'],
            'jpeg_encoding_verified': False, 'physical_playback_verified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output'):
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--deps', type=Path)
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    result = verify(extract_eg(a.source.read_bytes()), a.image.read_bytes(),
                    json.loads(a.report.read_text()), a.previous_image.read_bytes(),
                    json.loads(a.previous_report.read_text()))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(f'PASS {len(result["producer_cases"])} full native preview producer cases')


if __name__ == '__main__':
    main()
