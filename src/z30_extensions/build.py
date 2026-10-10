"""Compose stock-relative firmware features."""
from .plan import Plan
from .patch import Edit
from .features.liveview_dual import build as build_dual, SPECS as DUAL_SPECS
from .platform import z30_120 as z30_platform
from .platform import z7_380 as z7_platform

PLATFORMS = {'z30': z30_platform, 'z7': z7_platform}
PRESETS = ('full', 'liveview-dual', 'm900', 'shutter-sound')


def build(source, preset='full', model='z30'):
    if preset not in PRESETS:
        raise ValueError('Unknown feature preset')
    if model not in PLATFORMS:
        raise ValueError('Unknown camera model')
    platform = PLATFORMS[model]
    if preset == 'liveview-dual':
        return build_dual(source, model=model)
    plan = Plan(source, platform)
    if preset in ('full', 'liveview-dual'):
        dual, report = build_dual(source, model=model)
        plan.tail.extend(dual[len(source) - 2:-2])
        plan.edits.extend(Edit(int(e['offset'], 16), bytes.fromhex(e['expected_hex']),
                              bytes.fromhex(e['replacement_hex']), e['name']) for e in report['edits'])
        plan.features['liveview_dual'] = {'hooks': report['hooks'], 'mask_rom_address': report['mask_rom_address']}
    if preset in ('full', 'm900'):
        from .features.long_exposure import add as add_m900
        add_m900(plan, model=model)
    if preset in ('full', 'shutter-sound'):
        if model == 'z30':
            from .features.shutter_sound import add as add_sound
        else:
            from .features.shutter_sound_z7 import add as add_sound
        add_sound(plan)
    output, report = plan.finish()
    for address, expected in DUAL_SPECS[model]['video_stock_windows']:
        if output[address - platform.BASE:address - platform.BASE + len(expected)] != expected:
            raise ValueError('Video shutter differs from stock')
    report['preset'] = preset
    report['video_shutter_stock'] = True
    return output, report

# Backward-compatible alias used by the original build report check.
VIDEO_STOCK_WINDOWS = DUAL_SPECS['z30']['video_stock_windows']
