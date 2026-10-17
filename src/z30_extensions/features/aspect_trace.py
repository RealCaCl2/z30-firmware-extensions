# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Opt-in capture-contract trace: bounded RAM records, deferred menu SD dump.

No file service is called from geometry/capture callbacks. Records contain
parameters and addresses, never image data or arbitrary memory contents.
"""
from ..assembly import constant as c, invoke as n
from .sound_layout import SUCCESS

MAGIC = 0x31444157  # WAD1
CAPACITY, STRIDE, HEADER = 64, 160, 24
TRACE_BYTES = HEADER + CAPACITY * STRIDE


def install(plan, f, codes, sizes):
    for name, size in (('aspect_trace_event', 512), ('aspect_trace_config', 384),
                       ('aspect_trace_save', 768)):
        sizes[name] = size
        f[name] = plan.alloc(bytes(size))
    call = lambda name: n(f[name])
    path = plan.alloc(b'A:\\WL-ASPECT.DBG\0')
    codes['aspect_trace_event'] = f'''push {{r4-r11,lr}};sub sp,#4;
        mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;
        cmp r6,#144;bhi done;{call('aspect_get')}cmp r0,#0;beq done;mov r8,r0;
        ldr r0,[r8,#36];cmp r0,#0;bne done;
        ldr r0,[r8];cmp r0,#6;bhi done;
        cmp r4,#1;bne record;
        cmp r0,#2;bhi done;
        {c('r0',0x10132247)}cmp r7,r0;beq done;
        {c('r0',0x100aea9b)}cmp r7,r0;beq done;
        record:{c('r0',MAGIC)}str r0,[r8,#16];movs r0,#2;str r0,[r8,#20];
        movs r0,#{CAPACITY};str r0,[r8,#28];movs r0,#{STRIDE};str r0,[r8,#32];
        add r9,r8,#24;movs r2,#4;
        claim:ldrex r10,[r9];adds r1,r10,#1;strex r0,r1,[r9];cmp r0,#0;beq claimed;
        subs r2,#1;bne claim;b done;
        claimed:cmp r4,#2;beq config_slot;
        movs r1,#{CAPACITY-7};udiv r0,r10,r1;mls r0,r0,r1,r10;adds r0,#7;b slot;
        config_slot:ldr r0,[r8];
        slot:movs r1,#{STRIDE};mul r11,r0,r1;
        add r11,r8;adds r11,#40;mov r0,r11;movs r1,#40;movs r2,#0;
        clear:str r2,[r0],#4;subs r1,#1;bne clear;
        str r4,[r11,#4];ldr r0,[r8];str r0,[r11,#8];str r7,[r11,#12];
        add r0,r11,#16;mov r1,r5;mov r2,r6;cmp r2,#0;beq commit;
        copy:ldrb r3,[r1],#1;strb r3,[r0],#1;subs r2,#1;bne copy;
        commit:dmb ish;adds r10,#1;str r10,[r11];
        done:add sp,#4;pop {{r4-r11,pc}};'''
    codes['aspect_trace_config'] = f'''push {{r4-r8,lr}};sub sp,#144;
        mov r4,r0;mov r5,r1;mov r8,lr;mov r0,sp;movs r1,#144;{n(0x10199c40)}
        ldr r0,[r4];str r0,[sp];ldr r0,[r4,#0x98];str r0,[sp,#4];
        ldr.w r0,[r4,#0x104];str r0,[sp,#8];
        ldr.w r0,[r4,#0x208];str r0,[sp,#12];
        ldr.w r0,[r4,#0x224];str r0,[sp,#16];
        ldr.w r0,[r4,#0x228];str r0,[sp,#20];
        ldr.w r0,[r4,#0x22c];str r0,[sp,#24];
        ldr.w r0,[r4,#0x4d4];str r0,[sp,#28];
        add r0,sp,#32;mov r1,r5;movs r2,#28;
        copy:ldr r3,[r1],#4;str r3,[r0],#4;subs r2,#1;bne copy;
        movs r0,#2;mov r1,sp;movs r2,#144;{c('r3',0x10142a22)}{call('aspect_trace_event')}
        add sp,#144;pop {{r4-r8,pc}};'''
    codes['aspect_output'] = f'''push {{r4-r8,lr}};sub sp,#32;
        mov r4,r0;mov r5,r1;mov r6,lr;{c('r2',0xc18c7dfc)}
        ldr r0,[r2,r4,lsl #2];add r0,r0,r5,lsl #2;{call('aspect_translate')}mov r7,r0;
        str r4,[sp];str r5,[sp,#4];str r7,[sp,#8];ldr r0,[r7];str r0,[sp,#12];
        ldr r0,[sp,#32];str r0,[sp,#16];ldr r0,[sp,#36];str r0,[sp,#20];
        ldr r0,[sp,#40];str r0,[sp,#24];ldr r0,[sp,#44];str r0,[sp,#28];
        movs r0,#1;mov r1,sp;movs r2,#32;mov r3,r6;{call('aspect_trace_event')}
        mov r0,r7;add sp,#32;pop {{r4-r8,pc}};'''
    needle='ldrb r0,[r5];\n        push {r0,r1};'
    assert codes['aspect_narrow_source'].count(needle) == 1
    codes['aspect_narrow_source'] = codes['aspect_narrow_source'].replace(needle,
        "ldrb r0,[r5];push {r0-r3,r12,lr};mov r0,r5;mov r1,r4;"+
        call('aspect_trace_config')+"pop {r0-r3,r12,lr};push {r0,r1};")
    # Menu task only. The freeze flag drops trace events while the synchronous
    # file writer reads the ring. It never blocks capture or changes geometry.
    codes['aspect_trace_save'] = f'''push {{r4-r8,lr}};sub sp,#64;
        {call('aspect_get')}mov r4,r0;cmp r4,#0;beq done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        ldr r0,[r4,#16];{c('r1',MAGIC)}cmp r0,r1;bne done;
        movs r0,#1;str r0,[r4,#36];dmb ish;
        mov r0,sp;movs r1,#48;{n(0x10199c40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',path)}{n(0x1033829e)}
        mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382a2)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        movs r0,#0;str r0,[sp,#48];ldr r0,[sp,#8];
        add r1,r4,#16;{c('r2',TRACE_BYTES)}add r3,sp,#48;{n(0x102c8a6a)}mov r5,r0;
        mov r0,sp;{n(0x1033832e)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        cmp r5,#0;bne unfreeze;ldr r0,[sp,#48];{c('r1',TRACE_BYTES)}cmp r0,r1;
        unfreeze:movs r0,#0;dmb ish;str r0,[r4,#36];
        done:movs r0,#0;add sp,#64;pop {{r4-r8,pc}};'''
    needle="cmp r0,#0;beq done;"
    assert needle in codes['aspect_open']
    codes['aspect_open'] = codes['aspect_open'].replace(needle,
        needle+call('aspect_trace_save'),1)
    return {'version': 2, 'file': 'WL-ASPECT.DBG', 'bytes': TRACE_BYTES,
            'magic': hex(MAGIC), 'capacity': CAPACITY, 'stride': STRIDE,
            'dump': 'Open photo-aspect submenu after shooting; busy state skips dumping',
            'capture_file_io': False,
            'pinned_config_slots':7,'output_ring_capacity':CAPACITY-7,
            'output_profiles':[0,1,2],
            'excluded_output_callers':['0x10132247','0x100aea9b'],
            'events': {'1': 'Output selector caller, area, size, pointer, WH and saved registers',
                       '2': 'Normal configuration, capture flags and 112-byte geometry'}}
