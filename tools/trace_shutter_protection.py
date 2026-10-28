"""Offline shutter/shutdown research. Never accesses a camera or makes patches."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from inspect_precapture import bl_target, mapped_address

TARGETS = (0x101FCFF8, 0x101F8F60, 0x100F2782, 0x101B4E6C,
           0x101B4BD0, 0x101B4674, 0x101B69D8, 0x101B6138,
           0x1026E6DC, 0x102716A0)
WINDOWS = ((0x1005F4F6, 0x68), (0x1026E6DC, 0x250),
           (0x1026E8A6, 0x3C), (0x101FCFF8, 0x90),
           (0x101F8F60, 0x38), (0x101FD538, 0x5A),
           (0x101F8D1E, 0x98), (0x100F2782, 0x16),
           (0x10270928, 0x204), (0x1006AAEC, 0x20),
           (0x100686EE, 0x14), (0x101B4224, 0x50),
           (0x101B43AC, 0x2E), (0x100F2CC2, 0x40),
           (0x10594844, 0xB8), (0x101B6B1A, 0x2C),
           (0x101B6C02, 0x18), (0x101B4B74, 0x5C))


def main():
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    from capstone.arm import ARM_OP_MEM, ARM_REG_PC
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--window', action='append', default=[], help='Address:size, both accept 0x')
    args = p.parse_args()
    raw = args.source.read_bytes()
    data = raw if len(raw) == 33939514 else extract_eg(raw)
    stock = StockImage(data)
    def string(address):
        return stock.read(address, 200).split(b'\0')[0].decode('ascii', errors='replace')
    controllers = []
    for i in range(17):
        at = 0x114A25BC + i * 12
        ident, name, handler = stock.words(at, 3)
        assert ident == i and handler & 1
        getter = handler & ~1
        instructions = list(Cs(CS_ARCH_ARM, CS_MODE_THUMB).disasm(stock.read(getter, 4), getter))
        assert len(instructions) == 2 and instructions[0].mnemonic == 'ldr' and instructions[1].mnemonic == 'bx'
        encoded, = struct.unpack('<H', stock.read(getter, 2))
        assert encoded & 0xFF00 == 0x4800 and stock.read(getter+2, 2) == b'\x70\x47'
        descriptor, = stock.words(((getter+4) & ~3) + (encoded & 255)*4, 1)
        event_handler, = stock.words(descriptor, 1)
        controllers.append(dict(address=hex(at), id=ident, name=string(name),
                                descriptor_getter=hex(handler), descriptor=hex(descriptor),
                                event_handler=hex(event_handler)))
    commands = []
    for i in range(0x50):
        at = 0x114A2688 + i * 28
        words = stock.words(at, 7)
        assert words[0] == i
        route = list(stock.read(at+8, 16))
        route = route[:route.index(17)] if 17 in route else route
        assert all(c < 17 for c in route)
        commands.append(dict(address=hex(at), id=i, name=string(words[1]), route=route,
                             raw_words=[hex(w) for w in words]))
    refs = {hex(t): [] for t in TARGETS}
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    for offset in range(0, 0x740000, 2):
        a, b = struct.unpack_from('<HH', data, offset)
        target = bl_target(BASE + offset, a, b)
        if target in TARGETS:
            ins = list(md.disasm(data[offset:offset+4], BASE+offset))
            assert len(ins) == 1 and ins[0].mnemonic == 'bl'
            refs[hex(target)].append(hex(BASE + offset))
    pointers = {}
    for target in TARGETS:
        values = []
        needle = struct.pack('<I', target | 1)
        at = data.find(needle)
        while at >= 0:
            if at % 4 == 0:
                values.append(hex(mapped_address(at)))
            at = data.find(needle, at + 1)
        pointers[hex(target)] = values
    md.detail = True
    lines = ['Identity checked stock; linear windows include possible literal pools and tables.']
    windows = [(int(a, 0), int(b, 0)) for a, b in (s.split(':') for s in args.window)] or WINDOWS
    for address, size in windows:
        lines.append('\nWINDOW ' + hex(address))
        for ins in md.disasm(stock.read(address, size), address):
            text = f'{ins.address:08x} {ins.bytes.hex():12} {ins.mnemonic:9} {ins.op_str}'
            if ins.mnemonic.startswith('ldr'):
                for op in ins.operands:
                    if op.type == ARM_OP_MEM and op.mem.base == ARM_REG_PC:
                        at = ((ins.address + 4) & ~3) + op.mem.disp
                        value, = stock.words(at, 1)
                        text += f' ; [{at:08x}]={value:08x}'
            lines.append(text)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    assert stock.words(0x10725D3C, 1) == (0xC181A014,)
    assert stock.words(0xC181A01C, 1) == (0x10594845,)
    assert stock.words(0xC181A034, 1) == (0xC181A010,)
    assert stock.words(0xC181A010, 1) == (0x1059486D,)
    report = dict(stock_eg_sha256=sha256(data), controllers=controllers, commands=commands,
                  shutter_drive_record=dict(name_address='0x10725d1c', object='0xc181a014',
                                            input_validator='0x10594845',
                                            nested_descriptor='0xc181a010', executor='0x1059486d'),
                  bl_byte_candidates=refs, thumb_pointer_candidates=pointers,
                  shutdown_callbacks=[hex(w) for w in stock.words(0x114A2F4C, 20)],
                  capture_transition_table=list(stock.read(0x114E9EF4, 64)),
                  limitations=['BL byte patterns and pointers do not establish reachability.',
                               'Routes are controller ID bytes terminated by 0x11; real scheduling is not modeled.',
                               'No mechanical state or unpowered retention proof.'])
    for name, text in [('trace.json', json.dumps(report, indent=2)), ('windows.txt', '\n'.join(lines))]:
        with (args.output_dir / name).open('x', encoding='utf-8') as f:
            f.write(text + '\n')
    print(f'Saved {len(controllers)} controller descriptors, {len(commands)} command routes and instruction windows.')


if __name__ == '__main__':
    main()
