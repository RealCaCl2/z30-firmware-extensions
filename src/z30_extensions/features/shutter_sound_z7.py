# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Z7 sound menu and native SD-WAV audio hooks."""
import struct
from ..assembly import assemble, constant, invoke, jump
from .sound_layout_z7 import *
from .sound_audio_z7 import audio_code
from .sound_ui_z7 import ui_code


def _config_code(f, parser=0x1006B794):
    c = constant
    call = lambda name: invoke(f[name])
    return f"""
        push {{r0,r1,r4,lr}}; mov r4,r0;
        {c('r0',0xa21ab994)} ldrb r1,[r0,#28]; cmp r1,#18; bne stock;
        ldr r1,[r0,#32]; {c('r2',(f['complete'] | 1))} cmp r1,r2; bne stock;
        {call('get')} cmp r0,#0; beq stock;
        ldr r1,[r0,#{LENGTH}]; cmp r1,#0; beq stock;
        sub sp,#16; ldr r1,[r0,#{LENGTH}]; {c('r0',0x01010502)} str r0,[sp];
        str r1,[sp,#4]; movs r0,#0; str r0,[sp,#8]; mov r0,sp; {invoke(parser)};
        add sp,#16; pop {{r0,r1,r4,pc}};
        stock: ldr r0,[sp]; ldr r1,[sp,#4]; {invoke(parser)}; pop {{r0,r1,r4,pc}};
    """


def add(plan):
    im = plan.image
    if im.read(STATE_ANCHOR, 44) != bytes(44):
        raise ValueError('Sound state requires the unused Z7 page-zero slot')
    setup_header = list(im.words(SETUP_META_SOURCE, 7))
    stock_count = setup_header[5] & 0xFFFF
    if not 1 <= stock_count <= 200:
        raise ValueError('Unexpected Z7 settings row count')
    setup_count = stock_count + 1

    anchor_table = im.words(STATE_ANCHOR + 44 + 16, 1)[0]
    sizes = {'get': 128, 'cas': 96, 'validate': 384, 'convert': 192,
             'load': 1024, 'start': 448, 'stop': 192, 'trigger': 384,
             'complete': 384, 'open': 192, 'config': 320, 'data': 320,
             'shot': 192, 'ensure': 512, 'labels': 1024, 'render': 512,
             'action': 768, 'row': 256, 'getter': 256, 'route': 320, 'setup_init': 128}
    f = {name: plan.alloc(bytes(size)) for name, size in sizes.items()}
    tables = {i: plan.alloc(bytes(32)) for i in (PAGE_INDEX, FILES_INDEX, SWITCH_INDEX)}
    metas = {i: plan.alloc(bytes(28)) for i in tables}
    texts = dict(entry='Shutter sound', title='Shutter sound', files='Select sound', switch='Sound enabled',
                 enable='Enabled', file='Sound file', preview='Preview sound', reload='Reload sound',
                 off='OFF', on='ON', none='None', root='SHUTTER.WAV',
                 one='1.WAV', two='2.WAV', three='3.WAV', four='4.WAV', empty='', current='*',
                 missing='File not found', read='Read failed', invalid='Invalid WAV', close='Close failed',
                 busy='Sound busy', queue='Play failed', audio='Audio failed')
    strings = {key: plan.alloc(text.encode() + b'\0') for key, text in texts.items()}
    paths = [plan.alloc(path.encode() + b'\0') for path in ['A:\\SHUTTER.WAV'] + [f'A:\\SHUTTER\\{i}.WAV' for i in range(1, 5)]]
    path_table = plan.alloc(struct.pack('<5I', *paths))
    file_names = plan.alloc(struct.pack('<5I', *[strings[k] for k in ('root', 'one', 'two', 'three', 'four')]))
    fixed = plan.alloc(struct.pack('<15I', *[strings[k] for k in
        ('entry', 'title', 'enable', 'file', 'preview', 'reload', 'off', 'on', 'root', 'one', 'two', 'three', 'four', 'switch', 'files')]))
    errors = plan.alloc(struct.pack('<8I', *[strings[k] for k in ('title', 'missing', 'read', 'invalid', 'close', 'busy', 'queue', 'audio')]))
    file_ids = plan.alloc(struct.pack('<5I', *FILE_IDS))
    switch_ids = plan.alloc(struct.pack('<2I', *SWITCH_IDS))
    template = plan.alloc(struct.pack('<11I', (PAGE_INDEX << 16) | 2, SETUP_OBJECT, 0, 0,
                                    tables[PAGE_INDEX], 0, 0, 0, 0, 0, metas[PAGE_INDEX]))
    child_templates = {i: plan.alloc(struct.pack('<11I', (i << 16) | 2, 0, 0, 0, tables[i],
                                                0, 0, 0, 0, 0, metas[i])) for i in (FILES_INDEX, SWITCH_INDEX)}
    codes = audio_code(f, path_table, anchor_table)
    codes.update(ui_code(f, strings, fixed, file_names, errors, file_ids, switch_ids,
                         anchor_table, template, child_templates))
    codes['config'] = _config_code(f)
    c = constant
    call = lambda name: invoke(f[name])
    # Z7 data entry is 2-byte aligned, so the hook displaces ten bytes.
    # Reconstruct the real prologue in the fallback and resume at adds r5,#0x10.
    def _guard(else_label):
        return f"""{c('r0',0xa21ab994)} ldrb r1,[r0,#28]; cmp r1,#18; bne {else_label};
            ldr r1,[r0,#32]; {c('r2',(f['complete'] | 1))} cmp r1,r2; bne {else_label};
            {call('get')} cmp r0,#0; beq {else_label}; ldr r1,[r0,#{LENGTH}]; cmp r1,#0; beq {else_label};"""
    codes['data'] = f"""
        push {{r0-r4,lr}}; sub sp,#24; {_guard('stock')}
        {c('r1',PCM_OFFSET)} add r1,r0,r1; str r1,[sp]; ldr r1,[r0,#{LENGTH}]; str r1,[sp,#4];
        movs r1,#1; str r1,[sp,#8]; movs r1,#0; str r1,[sp,#12]; str r1,[sp,#16]; str r1,[sp,#20];
        ldr r1,[sp,#24]; mov r0,sp; {invoke(0x1006B94A)}
        add sp,#28; pop {{r1-r4,pc}};
        stock: add sp,#24; pop {{r0-r4,lr}}; push {{r4,r5,r6,lr}};
        {c('r4',0xa21ab994)} mov r6,r0; {c('r5',0xa188d1a0)} sub sp,#0x18;
        .balign 4; ldr.w pc,[pc,#0]; .word 0x1006BFF1;
    """

    codes['row'] = f"""
        push {{r0-r4,lr}}; {c('r4', SETUP_OBJECT)} cmp r0,r4; bne owned;
        ldrh r3,[r1,#24]; {c('r4', ENTRY_ID)} cmp r3,r4; bne restore;
        {call('labels')} b restore;
        owned: ldr r3,[r0]; {c('r4', (PAGE_INDEX << 16) | 2)} cmp r3,r4; beq labels;
        {c('r4', (FILES_INDEX << 16) | 2)} cmp r3,r4; beq labels;
        {c('r4', (SWITCH_INDEX << 16) | 2)} cmp r3,r4; bne restore;
        labels: {call('labels')}
        restore: pop {{r0-r4,lr}}; push.w {{r0-r11,lr}}; mov r8,r0; ldr r0,[r0,#0x28]; sub sp,#0xc;
        .balign 4; ldr.w pc,[pc,#0]; .word 0x104038A5;
    """
    # Z7 row continuation was located by the same disassembly alignment as the
    # liveview hooks.  Keep the original stock call target address.
    original_setup = im.words(im.words(SETUP_OBJECT + 16, 1)[0], 1)[0]
    codes['setup_init'] = f"""
        push {{r4,lr}}; mov r4,r0; {call('labels')} mov r0,r4;
        {invoke(original_setup)} pop {{r4,pc}};
    """
    code_sizes = {}
    for name, source in codes.items():
        raw = assemble(source, f[name], sizes[name])
        plan.put(f[name], raw)
        code_sizes[name] = len(raw)
    zero = plan.alloc(bytes.fromhex('00207047 01207047'))

    # Z7 event map: use the last stock settings row's dispatch map.
    source_map_addr = im.words(setup_header[4] + (stock_count - 1) * 4, 1)[0]
    source_events = [im.words(source_map_addr + i * 16, 4) for i in range(SOURCE_EVENTS_LENGTH)]

    def events(callback):
        words = []
        for event, fn, predicate, destination in source_events:
            if event in ACTIVATION_EVENTS:
                fn, predicate, destination = callback | 1, 0, ACTIVATION_DESTINATION
            elif fn not in NAVIGATION:
                fn, predicate = 0, 0
                destination = NEUTRAL_DESTINATION_B if destination == NEUTRAL_DESTINATION_B else NEUTRAL_DESTINATION_A
            words.extend((event, fn, predicate, destination))
        return plan.alloc(struct.pack('<' + 'I' * len(words), *words))

    entry_events = events(f['open'])
    owned_events = events(f['action'])
    setup_controls = im.read(setup_header[3], stock_count * 28)
    entry = bytearray(setup_controls[-28:])
    struct.pack_into('<I', entry, 0, setup_count)
    struct.pack_into('<I', entry, 16, 0x1201)
    struct.pack_into('<I', entry, 24, ENTRY_ID)
    controls_address = plan.alloc(setup_controls + entry)
    dispatch_address = plan.alloc(im.read(setup_header[4], stock_count * 4) + struct.pack('<I', entry_events))
    plan.edit(SETUP_META_SOURCE + 12, struct.pack('<2I', controls_address, dispatch_address), 'Z7 sound settings entry tables')
    plan.edit(SETUP_META_SOURCE + 20, struct.pack('<I', (setup_header[5] & 0xFFFF0000) | setup_count), 'Z7 settings row count with sound')
    plan.edit(im.words(SETUP_OBJECT + 16, 1)[0], struct.pack('<I', f['setup_init'] | 1), 'Seed Z7 sound entry before settings rows')

    # Stock leaf page 0x18f provides the native back bitmap/header layout.
    leaf = im.words(STATE_ANCHOR + 0x18F * 44, 11)
    leaf_header = list(im.words(leaf[10], 7))
    base_layout = bytearray(im.read(leaf_header[2], 96))
    struct.pack_into('<I', base_layout, 4, 2)
    struct.pack_into('<I', base_layout, 28, HEADER_OBJECT_ID << 16)

    used = set()
    for index in range(0x1000):
        try:
            page = im.words(STATE_ANCHOR + index * 44, 11)
        except (ValueError, struct.error):
            break
        if (page[0] >> 16) != index or (page[0] & 0xFFFF) != 2:
            continue
        try:
            meta = im.words(page[10], 7)
            for row in range(meta[5] & 0xFFFF):
                used.add(struct.unpack('<H', im.read(meta[3] + row * 28 + 24, 2))[0])
        except (ValueError, struct.error):
            continue
    if set(ROOT_IDS + FILE_IDS + SWITCH_IDS + (ENTRY_ID,)) & used:
        raise ValueError('Z7 custom control identifiers conflict with stock')

    pages = {}
    for index, count, label, value, title, identifiers in (
        (PAGE_INDEX, 4, 41, 54, 40, ROOT_IDS),
        (FILES_INDEX, 5, 47, 60, 53, FILE_IDS),
        (SWITCH_INDEX, 2, 45, 58, 52, SWITCH_IDS),
    ):
        controls = bytearray()
        for i in range(count):
            control = bytearray(entry)
            struct.pack_into('<I', control, 0, label + i)
            struct.pack_into('<I', control, 16, 0x1209)
            struct.pack_into('<I', control, 20, value + i if index != PAGE_INDEX or i < 2 else 56)
            struct.pack_into('<I', control, 24, identifiers[i])
            controls.extend(control)
        control_address = plan.alloc(controls)
        dispatch = plan.alloc(struct.pack('<' + 'I' * count, *([owned_events] * count)))
        layout = bytearray(base_layout)
        struct.pack_into('<I', layout, 0, title)
        layout_address = plan.alloc(layout)
        boxes, labels, values = [], [], []
        for row in range(count):
            y = 62 + row * 52
            boxes.extend((72, y, 702, y + 48))
            label_right = (350 if row < 2 else 696) if index == PAGE_INDEX else 550
            labels.extend((78, y + 4, label_right, y + 44))
            values.extend((366 if index == PAGE_INDEX else 570, y, 696, y + 48))
        rectangles = [plan.alloc(struct.pack('<' + 'h' * len(rect), *rect)) for rect in (boxes, labels, values)]
        geometry = plan.alloc(struct.pack('<4I', count, *rectangles))
        header = leaf_header.copy()
        header[1:5] = [geometry, layout_address, control_address, dispatch]
        header[5] = (header[5] & 0xFFFF0000) | count
        plan.put(metas[index], struct.pack('<7I', *header))
        plan.put(tables[index], struct.pack('<8I', f['render'] | 1, zero | 1, 0x10405261,
                                           (zero + 4) | 1, zero | 1, zero | 1, zero | 1, 0x10405261))
        pages[str(index)] = {'metadata': hex(metas[index]), 'functions': hex(tables[index]),
                             'count': count, 'controls': hex(control_address), 'layout': hex(layout_address),
                             'geometry': hex(geometry), 'back_icon': True, 'header_object_id': hex(HEADER_OBJECT_ID)}
    for site, name, length in ((0x1040389A, 'row', 10), (0x103FCCC2, 'getter', 12),
                               (0x10072728, 'shot', 8), (DATA_ENTRY, 'data', 10),
                               (0x1048B3FA, 'route', 12)):
        expected = im.read(site, length)
        plan.edit(site, jump(site, f[name], length), 'Z7 sound hook: ' + name, expected)
    # Config call site: patch the inlined BL to a cave trampoline that calls our tail config.
    cave = CONFIG_CAVE
    if im.read(cave, 16) != bytes(16):
        raise ValueError('Sound config trampoline cave is not free')
    trampoline = assemble('push {lr}; ' + constant('r12', f['config'] | 1) + ' blx r12; pop {pc};', cave, 16)
    plan.edit(cave, trampoline, 'Z7 sound config trampoline')
    bl = assemble('bl ' + hex(cave), CONFIG_CALL_SITE, 4)
    plan.edit(CONFIG_CALL_SITE, bl, 'Z7 sound config call', CONFIG_CALL_SITE_EXPECTED)

    plan.features['shutter_sound'] = {
        'functions': {k: hex(v | 1) for k, v in f.items()}, 'code_sizes': code_sizes,
        'pages': pages, 'state_anchor': hex(STATE_ANCHOR + 16), 'anchor_table': hex(anchor_table),
        'settings_count': setup_count, 'source_events': hex(source_map_addr),
        'activation_events': [hex(x) for x in ACTIVATION_EVENTS],
        'cave': {'address': hex(cave), 'trampoline_size': len(trampoline)},
        'default_enabled': False, 'settings_persistence': 'RAM only; reset at reboot',
        'sample_format': '8 kHz, mono, signed 16-bit, canonical 44-byte WAV, <=1.08 seconds',
        'card_type': 'CFexpress/XQD (A:)',
        'hardware_assumptions': {
            'sensor': 'Z7 FX/45.7MP vs Z30 DX/20.9MP; image pipeline is not modified',
            'card': 'CFexpress/XQD primary slot A:',
            'shutter': 'Z7 has mechanical/electronic shutter; shot callback layout was re-mapped',
            'display': 'Z7 native geometry tables were checked; 72..702 style rectangles exist on Z7',
        },
        'card_paths': ['SHUTTER.WAV'] + [f'SHUTTER/{i}.WAV' for i in range(1, 5)],
        'physical_menu_audio_verified': False,
        'physical_hardware_verified': False,
    }
