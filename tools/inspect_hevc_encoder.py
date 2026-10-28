"""Offline Z30 C1.20 encoder configuration/MMIO tracing; never accesses a camera.

Runs stock parameter construction and register serialization with synthetic
allocation results and RAM-backed MMIO. This does not run a video accelerator.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, sha256


def trace(data, width, height, fps, profile_override=None):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    u.mem_map(BASE, (len(data) + 4095) & ~4095)
    u.mem_write(BASE, data)
    source, destination, length, _ = SCATTER
    u.mem_map(destination, (length + 4095) & ~4095)
    u.mem_write(destination, data[source-BASE:source-BASE+length])
    u.mem_map(0x50000000, 0x20000)
    u.mem_map(0x24070000, 0x2000)
    config, output, stack, stop = 0x50000000, 0x50001000, 0x50018000, 0x5000f000
    substitutions, writes = [], []
    returned = False

    def code(uc, address, size, _):
        nonlocal returned
        if address == stop:
            returned = True
            uc.emu_stop()
        elif address in (0x102e9cc8, 0x103967bc):
            substitutions.append(hex(address))
            uc.reg_write(UC_ARM_REG_R0, config)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def memory(uc, access, address, size, value, _):
        if 0x24070000 <= address < 0x24072000:
            writes.append(dict(pc=hex(uc.reg_read(UC_ARM_REG_PC)),
                               address=hex(address), size=size, value=hex(value)))

    u.hook_add(UC_HOOK_CODE, code)
    u.hook_add(UC_HOOK_MEM_WRITE, memory)

    def call(address, args):
        nonlocal returned
        returned = False
        u.reg_write(UC_ARM_REG_SP, stack)
        u.reg_write(UC_ARM_REG_LR, stop | 1)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            u.reg_write(reg, value)
        u.emu_start(address | 1, 0, count=100000)
        assert returned, 'Instruction bound reached before stock return'
        assert u.reg_read(UC_ARM_REG_SP) == stack, 'Unbalanced stack'

    # Synthetic call context; modeled on the stock caller at 0x102C9C28.
    # It is not a capture of camera state or a validated recording mode.
    u.mem_write(stack, struct.pack('<15I', 144000000 >> 10, 144000000 >> 10,
                                  fps, 1, 20, 1, 0, 0, 0, 5, 1, 1, 1, 1, output))
    call(0x102dfbfe, [15, 1, width, height])
    assert u.reg_read(UC_ARM_REG_R0) == 0
    assert struct.unpack('<I', u.mem_read(output, 4))[0] == config
    before = bytes(u.mem_read(config, 0x1a0))
    if profile_override is not None:
        u.mem_write(config + 0x60, bytes([profile_override]))
    call(0x102dff5c, [config, 0, 0, 0])
    call(0x102e9de8, [before[0x61], 0, 0, 0])
    level_limit = u.reg_read(UC_ARM_REG_R0)
    return dict(width=width, height=height, synthetic_fps_code=fps,
                constructed_profile_byte=before[0x60], constructed_level_byte=before[0x61],
                native_level_macroblocks_per_second=level_limit,
                profile_override=profile_override, allocation_lookup_substitutions=substitutions,
                mmio_writes=writes)


def inspect(data):
    rows = [trace(data, w, h, f) for w, h, f in
            ((3840, 2160, 29), (1920, 1080, 59), (1920, 1080, 29))]
    # Differential serialization probe only: changing this byte does not prove
    # the accelerator supports the supplied value or another codec.
    rows.append(trace(data, 3840, 2160, 29, profile_override=1))
    assert all(row['constructed_profile_byte'] == 100 for row in rows)
    assert rows[0]['constructed_level_byte'] == 51
    assert rows[0]['native_level_macroblocks_per_second'] == 983040
    original, changed = rows[0]['mmio_writes'], rows[-1]['mmio_writes']
    assert len(original) == len(changed)
    differences = [(a, b) for a, b in zip(original, changed) if a != b]
    assert len(differences) == 1
    assert differences[0][0]['address'] == '0x24070e30'
    assert differences[0][0]['value'] == '0x64'
    assert differences[0][1]['value'] == '0x1'
    return dict(stock_eg_sha256=sha256(data), camera_access=False,
                differential_probe='Changing config +0x60 from 100 to 1 changes only the serialized write to 0x24070E30.',
                method='Stock ARM parameter construction and MMIO serialization in Unicorn',
                limitations=['Allocation/lookup calls return synthetic buffers.',
                             'MMIO is ordinary emulator RAM, without accelerator behavior.',
                             'Inputs are synthetic and no bitstream is generated.',
                             'Profile override is an offline field-dependency probe, not HEVC support.'],
                scenarios=rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = inspect(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    for row in result['scenarios']:
        print(row['width'], row['height'], row['synthetic_fps_code'],
              'profile', row['constructed_profile_byte'], 'level', row['constructed_level_byte'],
              'override', row['profile_override'], 'writes', len(row['mmio_writes']))


if __name__ == '__main__':
    main()
