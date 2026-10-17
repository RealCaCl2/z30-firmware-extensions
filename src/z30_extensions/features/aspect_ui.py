# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Experimental native photo-aspect selector and grid reference-frame hook."""
import struct
from ..assembly import constant as c, invoke as n, assemble, jump
from .aspect_geometry import PROFILES, build_trial, preview_dimensions

PAGE_INDEX = 961
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = 100, 101, 102, 109, 116
MAGIC = 0x32414C57  # WLA2: previous-area field now refers to the user setting.
AREA_KEY = 0x501
CAPTURE_AREA_KEY = 0x189E  # Derived state; never substitute for the user setting.
NAMES = ('OFF (stock)', '4:3', '5:4', '2:1', '2.35:1', '2.39:1', '65:24 XPAN')
SPANS = ((0x11571E18, 60), (0x115A4D4C, 0x1E0))
SIZES = dict(aspect_get=96, aspect_labels=384, aspect_open=160, aspect_action=448,
             aspect_save=640, aspect_restore=640, aspect_close=128,
             aspect_translate=256, aspect_index=160, aspect_trim=160,
             aspect_grid=704, aspect_grid_original=64, aspect_sync=256)

# Return addresses of the stock still-image buffer/geometry consumers. Generic
# file playback/editing callers are deliberately excluded: an old photo must
# never acquire the currently selected shooting ratio.
PREVIEW_CALLERS = {
    'small': (0x100A4F65, 0x100A51AD, 0x101434FD, 0x10143641,
              0x10146737, 0x10221B5B, 0x1038FAD3),
    'large': (0x10143545, 0x10143671, 0x101438DB, 0x1014676D, 0x10221B63),
}

PREVIEW_DIRECT_SPAN = (0x1157223C, 80)
# Literal reads in the independent preview pixel-buffer producers. Strides
# stay stock; the immutable copies only replace index-1 output dimensions.
PREVIEW_DIRECT_HOOKS = (
    (0x10142DE4, 10, 'r3', 0x11572250,
     'movs r2,#1;strb r2,[r4,#0x14];addw r1,r1,#0x1ff;',
     'ff4b0122227501f2ff11'),
    (0x10142E46, 12, 'r3', 0x11572250,
     'strb.w r2,[r4,#0x30];adds r3,#0x14;add.w r3,r3,r1,lsl #2;',
     'e74b84f83020143303eb8103'),
    (0x101437A2, 10, 'r0', 0x11572264,
     'add.w r1,r0,r1,lsl #2;ldrsh.w r0,[r1];',
     'f74800eb8101b1f90000'),
    (0x1014651C, 8, 'r0', 0x11572250,
     'strh.w r8,[r4,#4];subs r0,#0x14;',
     'f548a4f804801438'),
    (0x10146552, 12, 'r1', 0x11572250,
     'strb.w lr,[r4,#0x1c];adds r1,#0x14;add.w r1,r1,r2,lsl #2;',
     'e84984f81ce0143101eb8201'),
)


def prepare(plan, f, resources, allocation_size, *, trace=False):
    sizes = dict(SIZES)
    for key, size in SIZES.items():
        f[key] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Photo aspect', root='Photo aspect', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<7I', *(resources.alloc(v) for v in NAMES)))
    copies = []
    geometries = [(0, 0, 5568, 3712)]
    profiles = []
    for profile in PROFILES:
        image, report = build_trial(plan.source, profile)
        copies.append([plan.alloc(image[a-0x10020000:a-0x10020000+length]) for a, length in SPANS])
        geometries.append(tuple(report['target_large_in_dx_xywh']))
        profiles.append(dict(name=profile,ratio=report['ratio'],jpeg_wh=report['target_jpeg_wh'],
                             large_xywh=report['target_large_in_dx_xywh'],
                             preview_wh=[preview_dimensions(*report['target_jpeg_wh'][0], box)
                                         for box in ((640,360),(1920,1080))]))
    pointer_table = plan.alloc(struct.pack('<12I', *(v for row in copies for v in row)))
    geometry_table = plan.alloc(b''.join(struct.pack('<4I', *v) for v in geometries))
    path = plan.alloc(b'A:\\WL-ASPECT.CFG\0')
    call = lambda key: n(f[key])
    from .sound_settings import settings_code
    settings = settings_code(f, path, state_get='aspect_get', close='aspect_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=4, selected_max=6, load_sound=False, restore_enabled=True)
    codes = {new: settings[old] for old, new in
             (('save_settings', 'aspect_save'), ('restore_settings', 'aspect_restore'),
              ('settings_close', 'aspect_close'))}
    codes['aspect_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r1;done:pop {{r4,pc}};'''
    codes['aspect_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['root'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('aspect_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#6;it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r4,r5;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#7;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['aspect_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{call('aspect_sync')}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('aspect_get')}ldr r4,[r0];cmp r4,#6;it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r1;
        ASPECT_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # Restore the native backend together with the profile. A persisted choice
    # alone must never apply custom area-2 geometry while the camera stays DX.
    codes['aspect_sync'] = f'''push {{r4-r6,lr}};{call('aspect_get')}cmp r0,#0;beq done;
        ldr r4,[r0];subs r4,#1;cmp r4,#5;bhi done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;beq done;
        {c('r0',AREA_KEY)}movs r1,#2;{n(0x10465990)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # Keep the original area when enabling, and restore it when disabling.
    # Publish selection only after the native area setter reports success.
    codes['aspect_action'] = f'''push {{r4-r8,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#6;bhi done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        {call('aspect_get')}cmp r0,#0;beq done;mov r6,r0;ldr r7,[r6];
        cmp r5,#0;beq disable;cmp r7,#0;bne area;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#4;bhi zero;cmp r0,#1;beq zero;cmp r0,#3;bne prior;
        zero:movs r0,#0;prior:str r0,[r6,#8];
        area:movs r8,#2;b set_area;
        disable:cmp r7,#0;beq root;ldr r8,[r6,#8];cmp r8,#4;bhi fallback;cmp r8,#1;beq fallback;cmp r8,#3;bne set_area;
        fallback:movs r8,#0;
        set_area:{c('r0',AREA_KEY)}mov r1,r8;{n(0x10465990)}cmp r0,#1;bne done;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,r8;bne done;
        str r5,[r6];dmb ish;{call('aspect_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:movs r0,#2;movw r1,#955;{n(0x1045f732)}
        done:movs r0,#0;pop {{r4-r8,pc}};'''
    # Pure address translation to immutable per-profile tables. No ROM/RAM
    # geometry table is edited when the user changes the selected profile.
    codes['aspect_translate'] = f'''push {{r4-r6,lr}};mov r4,r0;{call('aspect_get')}
        cmp r0,#0;beq stock;ldr r5,[r0];subs r5,#1;cmp r5,#5;bhi stock;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne stock;
        {c('r1',SPANS[0][0])}subs r6,r4,r1;cmp r6,#{SPANS[0][1]};blo first;
        {c('r1',SPANS[1][0])}subs r6,r4,r1;{c('r2',SPANS[1][1])}cmp r6,r2;bhs stock;
        lsls r5,r5,#3;adds r5,#4;b translate;
        first:lsls r5,r5,#3;
        translate:{c('r1',pointer_table)}ldr r0,[r1,r5];add r0,r6;pop {{r4-r6,pc}};
        stock:mov r0,r4;pop {{r4-r6,pc}};'''
    codes['aspect_index'] = '''cmp r0,#0;bne one;cmp r1,#0;beq i0;cmp r1,#1;beq i1;cmp r1,#2;beq i2;b bad;
        one:cmp r0,#1;bne two;cmp r1,#1;beq i3;cmp r1,#2;beq i4;b bad;
        two:cmp r0,#2;bne bad;cmp r1,#2;beq i5;
        bad:movs r0,#6;bx lr;i0:movs r0,#0;bx lr;i1:movs r0,#1;bx lr;
        i2:movs r0,#2;bx lr;i3:movs r0,#3;bx lr;i4:movs r0,#4;bx lr;i5:movs r0,#5;bx lr;'''
    selectors = []
    def selector(name, address, size, source):
        sizes[name] = size
        f[name] = plan.alloc(bytes(size))
        codes[name] = source
        length = 8 if address % 4 == 0 else 10
        plan.edit(address, jump(address, f[name], length), 'Experimental aspect selector: ' + name)
        selectors.append(address)
    # Independent still-preview output dimensions. Keep the primary geometry
    # tables identical to v2; only capture consumers with preview aspect index
    # 1 (stock 16:9) can see these immutable per-profile pairs. Excluded callers
    # retain the exact stock pointer, even while a custom ratio is selected.
    for kind,address,base in (('small',0x1020C02E,0x115A4CFC),
                              ('large',0x1020C038,0x115A4D10)):
        column = 0 if kind == 'small' else 1
        table = plan.alloc(b''.join(struct.pack('<2H',*p['preview_wh'][column]) for p in profiles))
        callers = PREVIEW_CALLERS[kind]
        caller_table = plan.alloc(struct.pack('<'+'I'*len(callers),*callers))
        selector('aspect_preview_'+kind,address,320,f'''push {{r4-r8,lr}};
            mov r4,r0;mov r5,lr;{c('r6',base)}add r6,r6,r4,lsl #2;
            cmp r4,#1;bne original;
            {c('r1',caller_table)}movs r2,#{len(callers)};
            next_caller:ldr r3,[r1],#4;cmp r5,r3;beq allowed;
            subs r2,#1;bne next_caller;b original;
            allowed:{call('aspect_get')}cmp r0,#0;beq original;
            ldr r7,[r0];subs r7,#1;cmp r7,#5;bhi original;
            {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne original;
            {c('r0',table)}add r0,r0,r7,lsl #2;pop {{r4-r8,pc}};
            original:mov r0,r6;pop {{r4-r8,pc}};''')
    # The camera sample from v3 still contained 16:9 previews. These separate
    # pixel-buffer producers read dimensions directly, bypassing the getters.
    preview_shadows=[]
    preview_base,preview_length=PREVIEW_DIRECT_SPAN
    for profile in profiles:
        data=bytearray(plan.source[preview_base-0x10020000:preview_base-0x10020000+preview_length])
        struct.pack_into('<2H',data,4,*profile['preview_wh'][0])
        struct.pack_into('<2H',data,44,*profile['preview_wh'][1])
        preview_shadows.append(plan.alloc(data))
    preview_pointers=plan.alloc(struct.pack('<6I',*preview_shadows))
    sizes['aspect_preview_data']=192
    f['aspect_preview_data']=plan.alloc(bytes(192))
    codes['aspect_preview_data']=f'''push {{r4-r6,lr}};mov r4,r0;
        {call('aspect_get')}cmp r0,#0;beq unchanged;ldr r5,[r0];subs r5,#1;cmp r5,#5;bhi unchanged;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne unchanged;
        {c('r0',preview_base)}subs r6,r4,r0;cmp r6,#{preview_length};bhs unchanged;
        {c('r0',preview_pointers)}ldr r0,[r0,r5,lsl #2];add r0,r6;pop {{r4-r6,pc}};
        unchanged:mov r0,r4;pop {{r4-r6,pc}};'''
    for address,length,register,base,replay,expected in PREVIEW_DIRECT_HOOKS:
        name=f'aspect_preview_direct_{address:x}'
        sizes[name]=192;f[name]=plan.alloc(bytes(192))
        slot={'r0':0,'r1':4,'r3':12}[register]
        # Preserve the live lr value too: one producer uses it as a data
        # register. The stack return avoids clobbering a live r12 at continuation.
        codes[name]=f'''push {{r0-r3,r12,lr}};{c('r0',base)}
            {call('aspect_preview_data')}str r0,[sp,#{slot}];pop {{r0-r3,r12,lr}};
            {replay}push {{r0,r1}};{c('r0',(address+length)|1)}
            str r0,[sp,#4];pop {{r0,pc}};'''
        plan.edit(address,jump(address,f[name],length),'Aspect independent preview producer',bytes.fromhex(expected))
    selector('aspect_output', 0x1020BFDA, 256 if trace else 128, f'''push {{r4,lr}};{c('r2',0xc18c7dfc)}
        ldr r0,[r2,r0,lsl #2];add r0,r0,r1,lsl #2;{call('aspect_translate')}pop {{r4,pc}};''')
    for name, address, offset, fallback in (('aspect_internal',0x1020BFC8,0,0),
                                           ('aspect_reduced',0x1020C05C,0x28,8)):
        selector(name,address,160,f'''push {{r4,lr}};cmp r0,#0;bne fixed;
            {c('r0',0xc18c7de8+offset)}ldr r0,[r0,r1,lsl #2];add r0,r0,r2,lsl #2;
            {call('aspect_translate')}pop {{r4,pc}};
            fixed:{c('r0',0x115a4c80+fallback)}pop {{r4,pc}};''')
    selector('aspect_offset',0x1020C072,128,f'''push {{r4,lr}};
        {c('r1',0x115a4d4c)}add r0,r1,r0,lsl #2;{call('aspect_translate')}pop {{r4,pc}};''')
    for name,address,offset,fallback in (('aspect_crop',0x1020C07C,0x3c,0xc),
                                        ('aspect_edges',0x1020C0D2,0x50,0x10),
                                        ('aspect_desc_edges',0x1020C128,0x64,0x10)):
        selector(name,address,224,f'''push {{r4-r6,lr}};cmp r0,#0;bne fixed;
            mov r4,r1;mov r0,r2;mov r1,r3;{call('aspect_index')}mov r5,r0;
            {c('r0',0xc18c7de8+offset)}ldr r0,[r0,r4,lsl #2];add r0,r0,r5,lsl #2;
            {call('aspect_translate')}pop {{r4-r6,pc}};
            fixed:{c('r0',0x115a4c80+fallback)}pop {{r4-r6,pc}};''')
    # Horizontal trim comes from a direct PC-relative table reference.
    codes['aspect_trim'] = f'''push {{r0,r1,r3,lr}};{c('r0',0x11571e18)}
        {call('aspect_translate')}mov r2,r0;pop {{r0,r1,r3,lr}};
        ldrb r0,[r5,#0x229];add r1,r1,r1,lsl #1;
        {c('r12',0x101428b5)}bx r12;'''
    plan.edit(0x101428AA,jump(0x101428AA,f['aspect_trim'],10),'Aspect horizontal trim table',
              bytes.fromhex('f94a95f8290201eb4101'))
    # v5's speculative narrow-source rewrite failed on camera. Keep native
    # configuration unchanged; an opt-in diagnostic can still snapshot it.
    if trace:
        sizes['aspect_narrow_source']=192
        f['aspect_narrow_source']=plan.alloc(bytes(192))
        codes['aspect_narrow_source']=f'''strh.w r3,[r4,#0x4e];strh.w r3,[r4,#0x50];ldrb r0,[r5];
        push {{r0,r1}};{c('r0',0x10142a2d)}str r0,[sp,#4];pop {{r0,pc}};'''
        plan.edit(0x10142A22,jump(0x10142A22,f['aspect_narrow_source'],10),
                  'Aspect diagnostic normal configuration snapshot',bytes.fromhex('a4f84e30a4f850302878'))
    # The native normalizer rewrites lengths and counts, so override AFTER
    # it, restricted to its original mode-0 updater callsite. Other callers
    # and outputs retain the exact original normalization path.
    codes['aspect_grid_original'] = f'''push.w {{r4-r9,lr}};sub sp,#0x24;mov r6,r1;movs r2,#32;
        {c('r12',0x10454d51)}bx r12;'''
    codes['aspect_grid'] = f'''push {{r4-r11,lr}};sub sp,#4;mov r4,lr;
        {call('aspect_grid_original')}{c('r1',0x104be455)}cmp r4,r1;bne done;
        {call('aspect_get')}cmp r0,#0;beq done;ldr r5,[r0];cmp r5,#0;beq done;cmp r5,#6;bhi done;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne done;
        subw sp,sp,#0x468;mov r0,sp;{n(0x104b9348)}
        ldrsh r6,[sp,#8];ldrsh r7,[sp,#10];ldrsh r8,[sp,#12];ldrsh r9,[sp,#14];
        addw sp,sp,#0x468;subs r8,r8,r6;subs r9,r9,r7;
        cmp r6,#0;blt done;cmp r7,#0;blt done;cmp r8,#8;blt done;cmp r9,#8;blt done;
        cmp r8,#8192;bhi done;cmp r9,#8192;bhi done;
        {c('r0',geometry_table)}add r0,r0,r5,lsl #4;ldr r10,[r0,#8];ldr r11,[r0,#12];
        mul r0,r8,r11;mul r1,r9,r10;cmp r0,r1;bhs sides;
        mov r4,r8;udiv r5,r0,r10; b quant;
        sides:udiv r4,r1,r11;mov r5,r9;
        quant:bic r4,r4,#3;bic r5,r5,#3;cmp r4,#8;blt done;cmp r5,#8;blt done;
        sub r0,r8,r4;lsrs r0,#1;add r6,r0;bic r6,r6,#1;
        sub r0,r9,r5;lsrs r0,#1;add r7,r0;bic r7,r7,#1;
        {c('r0',0xc201a7f4)}strh r6,[r0];strh r7,[r0,#2];str r4,[r0,#4];
        subs r1,r5,#2;strh r1,[r0,#8];movs r1,#2;strh r1,[r0,#10];
        strh r6,[r0,#12];strh r7,[r0,#14];str r5,[r0,#16];
        subs r2,r4,#2;strh r2,[r0,#20];strh r1,[r0,#22];movs r1,#1;strh r1,[r0,#26];
        done:add sp,#4;pop {{r4-r11,pc}};'''
    plan.edit(0x10454D46,jump(0x10454D46,f['aspect_grid'],10),'Experimental aspect grid frame after normalization',
              bytes.fromhex('2de9f04389b00e462022'))
    trace_info=None
    if trace:
        from .aspect_trace import install
        trace_info=install(plan,f,codes,sizes)
    return dict(page_offset=page_offset,state_offset=state_offset,
                allocation_size=state_offset+16+(trace_info['bytes'] if trace_info else 0),
                trace=trace_info,
                codes=codes,geometry_table=geometry_table,selectors=selectors,
                function_names=list(codes),shadows=copies,sizes=sizes,profiles=profiles)


def finish(plan, f, extension, identifiers):
    ids=plan.alloc(struct.pack('<7I',*identifiers))
    extension['codes']['aspect_open']=extension['codes']['aspect_open'].replace('ASPECT_IDS;',c('r1',ids))
    for name,source in extension['codes'].items():
        plan.put(f[name],assemble(source,f[name],extension['sizes'][name]))
    plan.features['aspect_profiles']=dict(
        names=list(NAMES),default=0,functions={k:hex(f[k]|1) for k in extension['function_names']},
        page_index=PAGE_INDEX,page_offset=extension['page_offset'],state_offset=extension['state_offset'],
        geometry_table=hex(extension['geometry_table']),settings_file='WL-ASPECT.CFG',
        area_key=hex(AREA_KEY),capture_area_key=hex(CAPTURE_AREA_KEY),profile_area=2,shadows=extension['shadows'],
        config_magic=hex(MAGIC),config_version=2,
        preview_geometry_version=2,
        narrow_source_window_version=0,
        native_area_sync_version=1,
        diagnostic_trace=extension['trace'],
        preview_direct_hooks=[hex(v[0]) for v in PREVIEW_DIRECT_HOOKS],
        preview_capture_callers={k:[hex(v) for v in values] for k,values in PREVIEW_CALLERS.items()},
        preview_policy='Fit actual L aspect in stock preview allocation; four-pixel alignment; capture callers only',
        profiles=extension['profiles'],restore_policy='First custom-menu initialization; default OFF before initialization',
        preview='Experimental reference rectangle replaces enabled native grid, mode 0 only',
        status='experimental_menu_and_geometry_not_camera_validated',physical_verified=False,
        limitations=['Native grid must be enabled for the reference rectangle.',
                     'Mode 0 physical LCD identity, viewport semantics and pixel clearing require hardware validation.',
                     'Preview output dimensions are experimental; no physical playback verification of this image.',
                     'Fixed 160x120 thumbnail path remains stock.',
                     'No filled mask, live sensor/JPEG/NEF/HDMI or concurrent capture validation.',
                     'Alternate RAW/processing branches remain experimental; not a camera-ready update.'])
