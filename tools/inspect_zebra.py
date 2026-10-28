"""Offline Z30 C1.20 zebra settings and native request-builder research.

Requires capstone and unicorn. No camera access or firmware modification.
External services are explicit models, not hardware implementations.
"""
import argparse
import itertools
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256

WINDOWS = ((0x103D6F7A, 0x64), (0x103E4396, 0x54),
           (0x1042F00E, 0xD0), (0x10431A8E, 0x22),
           (0x10431B14, 0x3E), (0x1044F398, 0x72),
           (0x1044F412, 0x14), (0x1044F434, 0x98),
           (0x1045B5DC, 0x12), (0x1045B654, 0x18),
           (0x1045580C, 0xA), (0x104B6FCC, 0xC2),
           (0x104B73D2, 0x4), (0x104639F0, 0x124),
           (0x1042D87A, 0xC), (0x1042C2F8, 0x24),
           (0x103C57D0, 0x26), (0x103C12A2, 0x76),
           (0x101776FE, 0xB2), (0x101777C0, 0x8A),
           (0x10177916, 0x78), (0x101645AC, 0x44),
           (0x101644A4, 0x2C), (0x1006E36C, 0x56),
           (0x1018FD30, 0xCE), (0x1018FE02, 0x98),
           (0x10198170, 0x34), (0x1018F8D2, 0x96))
THRESHOLDS = (255, 248, 235, 224, 213, 202, 191, 180)


def inventory(stock):
    entries = []
    for index in range(347):
        pointer, = stock.words(0x1204EA5C + index * 4, 1)
        name = stock.read(pointer, 180).split(b'\0')[0].decode('ascii')
        if 'HighlightDisplay' not in name:
            continue
        key, = stock.words(0x1204EFC8 + index * 4, 1)
        group, local = divmod(key, 256)
        start, count = struct.unpack('<HH', stock.read(0x114F774C + group * 28 + 12, 4))
        if local >= count:
            raise ValueError('Property outside group')
        address = 0x114EB97C + (start + local) * 16
        kind, length, storage, source = stock.words(address, 4)
        entries.append(dict(name=name, key=hex(key), descriptor=hex(address),
                            kind=kind, length=length, storage=hex(storage),
                            default_source=hex(source),
                            rom_default=int.from_bytes(stock.read(source, length), 'little')))
    if {x['key'] for x in entries} != {'0x1182', '0x1183'}:
        raise ValueError('Unexpected highlight settings')
    return entries


def immediate_candidates(stock):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    hits = []
    for offset in range(0, 0x740000, 2):
        a, b = struct.unpack_from('<HH', stock.data, offset)
        if a & 0xFBF0 != 0xF240:
            continue
        value = (a & 15) << 12 | (a >> 10 & 1) << 11 | (b >> 12 & 7) << 8 | (b & 255)
        if value not in (0x1182, 0x1183, 0x189A):
            continue
        instructions = list(md.disasm(stock.data[offset:offset+4], BASE+offset))
        if len(instructions) != 1 or instructions[0].mnemonic != 'movw':
            raise ValueError('MOVW decoder disagreement')
        hits.append(dict(address=hex(BASE+offset), value=hex(value),
                         instruction=instructions[0].op_str))
    return hits


def caller_candidates(stock):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    hits = []
    for offset in range(0, 0x740000, 2):
        a, b = struct.unpack_from('<HH', stock.data, offset)
        if a & 0xF800 != 0xF000 or b & 0xD000 != 0xD000:
            continue
        sign = a >> 10 & 1
        i1, i2 = 1 ^ (b >> 13 & 1) ^ sign, 1 ^ (b >> 11 & 1) ^ sign
        immediate = sign << 24 | i1 << 23 | i2 << 22 | (a & 1023) << 12 | (b & 2047) << 1
        if sign:
            immediate -= 1 << 25
        if BASE + offset + 4 + immediate != 0x1044F398:
            continue
        ins = list(md.disasm(stock.data[offset:offset+4], BASE+offset))
        if len(ins) != 1 or ins[0].mnemonic != 'bl' or ins[0].op_str != '#0x1044f398':
            raise ValueError('BL decoder disagreement')
        hits.append(hex(BASE+offset))
    return hits


def probe(stock):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP,
                                   UC_ARM_REG_CPSR)
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    uc.mem_map(BASE, (len(stock.data) + 4095) & ~4095)
    uc.mem_write(BASE, stock.data)
    uc.mem_map(0xC1800000, 0x100000)
    source, destination, size, _ = SCATTER
    uc.mem_write(destination, stock.read(source, size))
    uc.mem_map(0x20000000, 0x10000)
    uc.mem_map(0x30000000, 0x1000)
    stop, stack = 0x30000000, 0x2000F000
    state = {}
    services = {0x10199C40, 0x10199CF0, 0x10465984, 0x1045B654,
                0x104639F0, 0x10455074, 0x1045580C}

    def hook(machine, address, _size, _context):
        if address == stop:
            state['returned'] = True
            machine.emu_stop()
            return
        if address not in services:
            return
        r0, r1, r2 = [machine.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        result = 0
        if address == 0x10199C40:
            machine.mem_write(r0, bytes(r1))
        elif address == 0x10199CF0:
            machine.mem_write(r0, bytes(machine.mem_read(r1, r2)))
        elif address == 0x10465984:
            result = {0x1183: state['threshold'], 0x189A: state['pattern']}[r0]
        elif address == 0x1045B654:
            result = state['route2']
        elif address == 0x104639F0:
            result = state['eligible']
        elif address == 0x10455074:
            result = int(bool(state['outputs'] & {1: 1, 4: 2}[r0]))
        elif address == 0x1045580C:
            state['requests'].append(dict(route=r0, channel=r1,
                                           payload=bytes(machine.mem_read(r2, 36)).hex()))
        machine.reg_write(UC_ARM_REG_R0, result)
        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

    uc.hook_add(UC_HOOK_CODE, hook)
    count = 0
    samples = []
    # Invalid settings and output combinations are intentional synthetic inputs.
    cases = itertools.product((*range(8), 8, 255), range(4), range(3),
                              range(2), range(2), range(2), range(4))
    for threshold, pattern, action, latch, route2, eligible, outputs in cases:
        state.clear()
        state.update(threshold=threshold, pattern=pattern, action=action, latch=latch,
                     route2=route2, eligible=eligible, outputs=outputs, requests=[], returned=False)
        uc.mem_write(0xC1821E60, struct.pack('<I', latch))
        uc.reg_write(UC_ARM_REG_CPSR, 0x30)
        uc.reg_write(UC_ARM_REG_SP, stack)
        uc.reg_write(UC_ARM_REG_LR, stop | 1)
        uc.reg_write(UC_ARM_REG_R0, action)
        uc.emu_start(0x1044F399, stop + 2, count=1000)
        if not state['returned'] or uc.reg_read(UC_ARM_REG_SP) != stack:
            raise AssertionError('Native routine did not return with balanced stack')
        expected_latch = action if action in (0, 1) else latch
        if int.from_bytes(uc.mem_read(0xC1821E60, 4), 'little') != expected_latch:
            raise AssertionError('Latch mismatch')
        enabled = int(bool(expected_latch and pattern))
        expected_routes = ([2] if route2 else []) + ([1, 4] if outputs else [])
        if [r['route'] for r in state['requests']] != expected_routes:
            raise AssertionError('Unexpected output dispatch')
        for request in state['requests']:
            data = bytes.fromhex(request['payload'])
            expected_enabled = enabled if request['route'] != 2 or eligible else 0
            actual_threshold, = struct.unpack_from('<H', data, 8)
            if (int.from_bytes(data[:4], 'little') != expected_enabled or
                    actual_threshold != (THRESHOLDS[threshold] if threshold < 8 else 248) or
                    data[32:34] != bytes([{1: 0, 2: 1}.get(pattern, 2)] * 2) or
                    int.from_bytes(data[10:12], 'little') != (13 if request['route'] == 4 else 15)):
                raise AssertionError(f'Unexpected native request: {state}')
        if (pattern, action, latch, route2, eligible, outputs) == (1, 1, 0, 1, 1, 1):
            samples.append(dict(threshold=threshold, requests=state['requests']))
        count += 1
    return dict(cases=count, passed=True, threshold_bytes=list(THRESHOLDS), samples=samples,
                modeled_services=[hex(x) for x in sorted(services)],
                scope='Unmodified native 0x1044f398 request construction and dispatch; external services modeled',
                camera_verified=False, pixels_verified=False, photo_mode_enabled=False)


def main():
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    from capstone.arm import ARM_OP_MEM, ARM_REG_PC
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('../Z_30_0120.bin'))
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    raw = args.source.read_bytes()
    stock = StockImage(raw if len(raw) == 33939514 else extract_eg(raw))
    report = dict(stock_eg_sha256=sha256(stock.data), settings=inventory(stock),
                  request_builder_bl_candidates=caller_candidates(stock),
                  derivation_binding=dict(address='0x114e8f8c',
                      words=[hex(x) for x in stock.words(0x114E8F8C, 6)],
                      dependency_keys=[hex(x) for x in stock.words(0x114E0828, 34)],
                      effective_pattern_callback='0x101776fe',
                      availability_callback='0x1017784a', output_property='0x189a'),
                  derivation_registry=dict(base='0x114e67e4', stride=24, count=521,
                      highlight_index=(0x114E8F8C-0x114E67E4)//24,
                      enumerate_function='0x1006e36c', lookup_function='0x1006e392',
                      service_function='0x1018fe4a', selection_function='0x1018fd30',
                      scope='Registry and service instruction leads; use QEMU audit for execution evidence'),
                  movw_candidates=immediate_candidates(stock), native_probe=probe(stock),
                  limitations=['Halfword-scanned MOVW sites are candidates, not a complete call graph.',
                               'Property derivation scheduling and queue consumption are not exercised by this inspector.',
                               'Numeric output routes do not establish LCD, EVF or HDMI identity.',
                               'No sensor frames, display pixels, capture files or hardware services are modeled.'])
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    lines = ['Stock instruction windows. Literal pools and jump tables may decode as instructions.']
    for start, size in WINDOWS:
        lines.append('\nWINDOW ' + hex(start))
        for ins in md.disasm(stock.read(start, size), start):
            line = f'{ins.address:08x} {ins.bytes.hex():12} {ins.mnemonic:9} {ins.op_str}'
            if ins.mnemonic.startswith('ldr'):
                for operand in ins.operands:
                    if operand.type == ARM_OP_MEM and operand.mem.base == ARM_REG_PC:
                        at = ((ins.address + 4) & ~3) + operand.mem.disp
                        line += f' ; [{at:08x}]={stock.words(at, 1)[0]:08x}'
            lines.append(line)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path in (args.output_dir / 'report.json', args.output_dir / 'stock-windows.txt'):
        if path.resolve() == args.source.resolve():
            raise ValueError('Output must differ from source')
    (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (args.output_dir / 'stock-windows.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(settings=report['settings'], cases=report['native_probe']['cases'],
                          passed=True, camera_verified=False), indent=2))


if __name__ == '__main__':
    main()
