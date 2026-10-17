"""Compose stock-relative firmware features."""
from .plan import Plan
from .patch import Edit
from .features.liveview_dual import build as build_dual, VIDEO_STOCK_WINDOWS
from .platform.z30_120 import BASE


PRESETS = ('full', 'liveview-dual', 'shutter-sound', 'm900', 'mtp-fast', 'module-host', 'video-rot',
           'bitrate-slowmo-uncap', 'aspect-profiles')
LEGACY_PRESETS = ('modular',)
FEATURE_ORDER = ('liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'module-host', 'video-rot',
                 'bitrate-slowmo-uncap', 'aspect-profiles')
FULL_FEATURES = FEATURE_ORDER
MODULAR_FEATURES = ('liveview-dual', 'm900', 'shutter-sound', 'module-host')


def resolve_presets(preset='full'):
    """Expand aliases once, independent of selection order or duplicates."""
    selected = [preset] if isinstance(preset, str) else list(preset)
    if not selected:
        raise ValueError('Select at least one feature preset')
    features = set()
    for name in selected:
        if name not in PRESETS + LEGACY_PRESETS:
            raise ValueError(f'Unknown feature preset: {name}')
        if name == 'full':
            features.update(FULL_FEATURES)
        elif name == 'modular':
            features.update(MODULAR_FEATURES)
        else:
            features.add(name)
    # Module host and bitrate profiles share the sound menu/state framework.
    if any(f in features for f in ('module-host', 'bitrate-slowmo-uncap', 'aspect-profiles')):
        features.add('shutter-sound')
    return list(dict.fromkeys(selected)), [f for f in FEATURE_ORDER if f in features]


def build(source, preset='full', *, aspect_trace=False):
    selected, features = resolve_presets(preset)
    if aspect_trace and 'aspect-profiles' not in features:
        raise ValueError('Aspect trace requires aspect-profiles')
    if features == ['liveview-dual']:
        output, report = build_dual(source)
        report.update(preset=selected[0] if len(selected) == 1 else selected,
                      selected_presets=selected, enabled_features=features)
        return output, report
    plan = Plan(source)
    if 'liveview-dual' in features:
        dual, report = build_dual(source)
        plan.tail.extend(dual[len(source) - 2:-2])
        plan.edits.extend(Edit(int(e['offset'], 16), bytes.fromhex(e['expected_hex']),
                              bytes.fromhex(e['replacement_hex']), e['name']) for e in report['edits'])
        plan.features['liveview_dual'] = {key: report[key] for key in
                                        ('hooks', 'mask_rom_address', 'full_info_disp', 'full_info_hooks')}
    if 'm900' in features:
        from .features.long_exposure import add
        add(plan)
    if 'shutter-sound' in features:
        from .features.shutter_sound import add
        add(plan, modules='module-host' in features, bitrate='bitrate-slowmo-uncap' in features,
            aspect='aspect-profiles' in features, aspect_trace=aspect_trace)
    if 'mtp-fast' in features:
        from .features.mtp_fast import add
        add(plan)
    if 'video-rot' in features:
        from .features.video_rot import add
        add(plan)
    if 'bitrate-slowmo-uncap' in features:
        from .features.bitrate_slowmo_uncap import add
        add(plan)
    output, report = plan.finish()
    for address, expected in VIDEO_STOCK_WINDOWS:
        if output[address - BASE:address - BASE + len(expected)] != expected:
            raise ValueError('Video shutter differs from stock')
    report['preset'] = selected[0] if len(selected) == 1 else selected
    report['selected_presets'] = selected
    report['enabled_features'] = features
    report['video_shutter_stock'] = True
    return output, report
