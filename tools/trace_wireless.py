"""Reproduce bounded static wireless investigation; not a complete call graph."""
import argparse
import array
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from probe_wireless_config import SOURCE, DESTINATION, SIZE, WRAPPERS, runtime


WINDOWS = (
    (0x10396844, 0x78),  # Outer loader; ROM source, RAM destination, size word.
    (0x11D4EF24, 0x4E),  # Embedded firmware selector.
    (0x11D0FB90, 0x70),  # Initialization caller.
    (0x11D4F058, 0x50),  # Stack defaults.
    (0x11D4F34C, 0x4E),  # Conditional A-MSDU initialization.
    (0x11D50704, 0x30), (0x11D50808, 0x1A),  # nmode = 1 path.
    (0x11D4F5C4, 0x40),  # Conditional bandwidth configuration.
    (0x11D531DE, 0x100),  # VHT query/set wrapper.
    (0x11D5E110, 0x52), (0x11D5E6F2, 0x1A),
    (0x11D5E778, 0x12), (0x11D5F28E, 0x1A),
    (0x11D5F2BA, 0x34), (0x11D5FA36, 0x1A),
    (0x11D5D9EC, 0x52), (0x11D5DA3E, 0xA0),
    (0x11D2E73E, 0x90),  # Diagnostic strings linked to echo replies.
    (0x11CB87F0, 0x38), (0x11CB8828, 0x2A),  # Socket service paths.
    (0x1031EAB0, 0x44), (0x10339EAC, 6), (0x1031E7C2, 0x12),
    (0x1039C3AC, 0x16), (0x103B6AC2, 0x30), (0x103B6F22, 6),
    (0x103B6992, 0x40), (0x104836B6, 0x72),
    (0x11CB8256, 0x14), (0x11CB8260, 0x1A), (0x11CB85A0, 0x48),
    (0x11CB7D98, 0x42), (0x11CB7DE8, 0x62),
    (0x11CB7FB6, 0x36), (0x11CB801C, 0x4E),
    (0x11D324CC, 0x1A),  # Socket option 0x1001 -> protocol option 12.
    (0x11D52A12, 0x5A), (0x11D5F6FA, 0x44),  # Band set/get wrappers.
    (0x11D0FB38, 0xC8), (0x11D0FC00, 0x12),  # Initialization mode and forced band.
    (0x11D0FEC6, 0x46), (0x11D0FF0A, 0xBA),  # AP channel selection.
    (0x11D46C18, 0x84),  # Existing two-band switch/check paths.
    (0x11D0E714, 0xB2),  # Group/command dispatcher, including group 0x19.
    (0x11D106DC, 0x7C), (0x11D10772, 0xBA),  # Country/profile callback (skip jump data).
    (0x11D50D14, 0x4A),  # Bandwidth state setter.
    (0x103B5F58, 0xA2), (0x103C792E, 0x3A), (0x103C7D34, 0x30),
    (0x10482DA6, 0x22), (0x104B2972, 0x26), (0x104B2B2C, 0x18),
    (0x11CB7448, 0x26), (0x11CB746E, 0xC), (0x11CB7A72, 0xE),
    (0x11D150D0, 0x3A), (0x11D16620, 0x36),  # Host-to-country request bridge.
    (0x11D5DC10, 0x1C), (0x11D5D9EC, 0x52),
    (0x11D4BF7A, 0x56), (0x11D4C03E, 0xC),
    (0x11D61EA4, 0x40), (0x11D6016E, 8), (0x11D6002C, 0x68),
    (0x101F5022, 0x2A), (0x101F5088, 0x46),  # Cached profile validation/lookup.
    (0x103A72C8, 0x6E), (0x103B5A28, 0x48),  # Source request construction.
    (0x1035AD56, 0x34), (0x10393C46, 0x54), (0x10393DCC, 0x80),
    (0x11D5FD0A, 0x36), (0x11D5DC5E, 0x1C),  # Chanspec format and interface version.
    (0x11D53718, 0x80), (0x11D5379A, 0x5E),  # Per-channel information processing.
    (0x11D5FB72, 0x50), (0x11D5FBC0, 0x20),
)


def bl_target(address, first, second):
    if first & 0xF800 != 0xF000 or second & 0xD000 != 0xD000:
        return None
    sign = (first >> 10) & 1
    i1 = 1 ^ ((second >> 13) & 1) ^ sign
    i2 = 1 ^ ((second >> 11) & 1) ^ sign
    value = (sign << 24 | i1 << 23 | i2 << 22 |
             (first & 0x3FF) << 12 | (second & 0x7FF) << 1)
    if sign:
        value -= 1 << 25
    return (address + 4 + value) & 0xFFFFFFFF


def trace(data):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    StockImage(data)
    assert struct.unpack_from('<I', data, 0x11EA309C - BASE)[0] == SIZE
    assert struct.unpack_from('<I', data, SOURCE - BASE + 0x20)[0] == DESTINATION + 0x60
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    targets = {entry: name for name, (entry, _) in WRAPPERS.items()}
    targets.update({0x11D4EF24: 'select_fw', 0x11D5DA3E: 'iovar_builder',
                    0x11D52A12: 'set_band', 0x11D5F6FA: 'band_ioctl'})
    refs = {name: [] for name in targets.values()}
    words = array.array('H', data[:len(data) & ~1])
    if sys.byteorder != 'little':
        words.byteswap()
    for index in range(len(words) - 1):
        address = BASE + index * 2
        target = bl_target(address, words[index], words[index + 1])
        if target not in targets:
            continue
        decoded = list(decoder.disasm(data[index * 2:index * 2 + 4], address))
        assert len(decoded) == 1 and decoded[0].mnemonic == 'bl'
        assert int(decoded[0].op_str.lstrip('#'), 16) == target
        refs[targets[target]].append(hex(address))
    windows = []
    for address, size in WINDOWS:
        embedded = SOURCE <= address < SOURCE + SIZE
        load = runtime(address) if embedded else address
        instructions = [
            {'address': hex(i.address), 'bytes': i.bytes.hex(),
             'mnemonic': i.mnemonic, 'operands': i.op_str}
            for i in decoder.disasm(data[address - BASE:address - BASE + size], load)
        ]
        windows.append({'rom_start': hex(address), 'decode_address': hex(load),
                        'instructions': instructions})
    return {
        'stock_eg_sha256': sha256(data),
        'embedded_image': {'rom_source': hex(SOURCE), 'ram_destination': hex(DESTINATION),
                           'size': hex(SIZE), 'size_word_rom': '0x11ea309c'},
        'country_table': {
            'runtime_address': '0xc1201f34',
            'entries': [data[SOURCE - BASE + 0x201F34 + i * 4:SOURCE - BASE + 0x201F34 + i * 4 + 4]
                        .split(b'\0')[0].decode('ascii') for i in range(39)],
        },
        'board_configuration_strings': [
            {'rom_address': hex(BASE + data.index(value)), 'text': value.decode('ascii')}
            for value in (b'bcm943455wlsagb_Type1MW_Nikon_SAR_180319.txt',
                          b'aa2g=1', b'aa5g=1', b'rxchain=1', b'txchain=1')
        ],
        'send_dispatch_table': {
            'group': 4, 'table_runtime': '0xc11fffbc', 'command': 5,
            'handler_word': hex(struct.unpack_from('<I', data, SOURCE - BASE + 0x1FFFBC + 5 * 4)[0]),
            'expected_handler': hex(runtime(0x11CB85A0) | 1),
        },
        'bl_encoding_candidates_capstone_checked': refs,
        'windows': windows,
        'limitations': [
            'BL encodings can occur in data; independent decoding does not prove reachability.',
            'Selected windows have analyst-selected boundaries and do not form a full call graph.',
            'RAM image mapping supported by outer loader constants and vector table.',
            'No real hardware, active configuration, or throughput validation.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capstone-path', type=Path, help='Optional existing Capstone module directory')
    args = parser.parse_args()
    if args.capstone_path:
        sys.path.insert(0, str(args.capstone_path))
    report = trace(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Saved {len(report['windows'])} instruction windows and checked BL candidates")


if __name__ == '__main__':
    main()
