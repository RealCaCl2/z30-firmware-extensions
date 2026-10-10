"""Standalone histogram + horizon feature; preserves native eligibility gates."""
import struct
from ..patch import Edit, apply_patch
from ..thumb import Thumb
from ..platform import z30_120 as z30_platform
from ..platform import z7_380 as z7_platform

SPECS = {
    'z30': {
        'platform': z30_platform,
        'gate': 0x1044FF70,
        'gate_expected': bytes.fromhex('8d4810b5002490f89601022812d1'),
        'widget_gate': 0x1036E8B4,
        'widget_expected': bytes.fromhex('90f89601022803d1'),
        'snapshot': 0xC2016A68,
        'ldrb_cmp': '90f89601 0228',
        'histogram': 0xC1826714,
        'binding': 0x1073F830 + 31 * 4,
        'masks_expected': (0x444,) * 9,
        'masks_replacement': (0xCCC,) * 9,
        'video_stock_windows': (
            (0x1016E150, bytes.fromhex('524b103310e0089c')),
            (0x1016E09E, bytes.fromhex('0fd1')),
        ),
    },
    'z7': {
        'platform': z7_platform,
        'gate': 0x103EBB6C,
        'gate_expected': bytes.fromhex('2e4810b5002490f88a01022812d1'),
        'widget_gate': 0x1036D58C,
        'widget_expected': bytes.fromhex('90f88a01022803d1'),
        'snapshot': 0xA201D4D0,
        'ldrb_cmp': '90f88a01 0228',
        'histogram': 0xA1827404,
        'binding': 0x1064D610 + 31 * 4,
        'masks_expected': (0x444,) * 6 + (0x440,) * 3,
        'masks_replacement': (0xCCC,) * 9,
        'video_stock_windows': (
            (0x10150770, bytes.fromhex('934b10330fe0019d')),
            (0x101506A6, bytes.fromhex('10d1')),
        ),
    },
}


def gate_code(address, prologue, yes, no, snapshot, ldrb_cmp):
    a = Thumb(address)
    if prologue:
        a.emit('10b5 0024')  # Native push {r4,lr}; movs r4,#0.
        a.load(0, 'snapshot', snapshot)
    a.emit(ldrb_cmp)  # ldrb.w r0,[r0,#state]; cmp r0,#2.
    a.branch(0, 'accepted')
    a.emit('0328')
    a.branch(0, 'accepted')
    a.load(3, 'no', no | 1)
    a.emit('1847')
    a.label('accepted')
    a.load(3, 'yes', yes | 1)
    a.emit('1847')
    return a.finish()


def build(source, model='z30'):
    spec = SPECS[model]
    platform = spec['platform']
    stock = platform.StockImage(source)
    source = stock.data
    if stock.words(spec['binding'], 1) != (spec['histogram'],):
        raise ValueError('Unexpected histogram descriptor binding')
    if stock.words(spec['histogram'] + 4, 9) != spec['masks_expected']:
        raise ValueError('Unexpected histogram visibility masks')
    for address, expected in spec['video_stock_windows']:
        if stock.read(address, len(expected)) != expected:
            raise ValueError('Video shutter source differs from stock')
    append = bytearray()
    edits = []
    hooks = []
    for site, expected, prologue, yes, no in (
        (spec['gate'], spec['gate_expected'], True, 0x103EBB7A if model == 'z7' else 0x1044FF7E,
         0x103EBBA0 if model == 'z7' else 0x1044FFA4),
        (spec['widget_gate'], spec['widget_expected'], False,
         0x1036D594 if model == 'z7' else 0x1036E8BC,
         0x1036D59C if model == 'z7' else 0x1036E8C4),
    ):
        if site % 4 or stock.read(site, len(expected)) != expected:
            raise ValueError('Unexpected histogram instruction window')
        entry = platform.BASE + len(source) - 2 + len(append)
        code = gate_code(entry, prologue, yes, no, spec['snapshot'], spec['ldrb_cmp'])
        jump = bytes.fromhex('dff800f0') + struct.pack('<I', entry | 1)
        jump += bytes.fromhex('00bf') * ((len(expected) - 8) // 2)
        edits.append(Edit(site - platform.BASE, expected, jump, 'Histogram DISP gate'))
        append.extend(code)
        hooks.append({'site': hex(site), 'entry': hex(entry | 1), 'size': len(code),
                      'resume_accept': hex(yes), 'resume_reject': hex(no)})
    mask_address = stock.rom(spec['histogram'] + 4, 36)
    edits.append(Edit(mask_address - platform.BASE, struct.pack('<9I', *spec['masks_expected']),
                      struct.pack('<9I', *spec['masks_replacement']), 'Histogram visibility masks'))
    output, report = apply_patch(source, edits, append, expected_sha256=platform.STOCK_SHA256,
                                 base=platform.BASE, capacity=platform.CAPACITY)
    for address, expected in spec['video_stock_windows']:
        if output[address - platform.BASE:address - platform.BASE + len(expected)] != expected:
            raise ValueError('Video shutter bytes were modified')
    report.update(feature='liveview_dual', preset='liveview-dual', model=platform.MODEL, firmware=platform.FIRMWARE,
                  hooks=hooks, mask_rom_address=hex(mask_address),
                  camera_verified=False, video_shutter_stock=True,
                  limitations=[
                      'Offline instruction checks do not verify full camera operation.',
                      'Native display eligibility and output-mode restrictions remain.',
                      'No Camera Status, CFW version marker or shutter sound is included.',
                      'Feature builder uses validated EG; the CLI extracts it internally from the stock container.',
                  ])
    report['features'] = {'liveview_dual': {'hooks': hooks, 'mask_rom_address': hex(mask_address)}}
    return output, report

# Backward-compatible Z30 aliases used by the original verification tooling.
_Z30 = SPECS['z30']
SNAPSHOT = _Z30['snapshot']
HOOKS = (
    (_Z30['gate'], _Z30['gate_expected'], True, 0x1044FF7E, 0x1044FFA4),
    (_Z30['widget_gate'], _Z30['widget_expected'], False, 0x1036E8BC, 0x1036E8C4),
)
VIDEO_STOCK_WINDOWS = _Z30['video_stock_windows']
