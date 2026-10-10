# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Z30 Firmware Extensions contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Native Z30 sound routines and menu support."""
from .sound_layout_z7 import *
from ..assembly import constant, invoke

def ui_code(f, strings, fixed, file_names, errors, file_ids, switch_ids, anchor_table, template, child_templates):
    code = {}
    c = constant
    def call(name): return invoke(f[name])
    native = lambda address: invoke(address, thumb=address != 0x10170a1c)
    code['ensure']=f"""
            push {{r4-r6,lr}}; {call('get')} cmp r0,#0; bne done;
            {c('r0',NEW_SIZE+31)} movs r1,#0; {native(0x1016fe38)} cmp r0,#0; beq done;
            mov r6,r0; adds r0,#31; bic r4,r0,#31;
            mov r0,r4; {c('r1',NEW_SIZE)} {native(0x10170a80)}
            mov r0,r4; {c('r1',anchor_table)} movs r2,#32; {native(0x10170a1c)}
            add r0,r4,#{PAGE_OFFSET}; {c('r1',template)} movs r2,#44; {native(0x10170a1c)}
            {c('r5',FILES_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[FILES_INDEX])} movs r2,#44; {native(0x10170a1c)} add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];
            {c('r5',SWITCH_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[SWITCH_INDEX])} movs r2,#44; {native(0x10170a1c)} add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];
            {c('r0',MAGIC)} str r0,[r4,#56];str r6,[r4,#60];mvn r0,#0;str r0,[r4,#112];
            dmb ish;{c('r0',STATE_ANCHOR)} str r4,[r0,#16];mov r0,r4;
            done: pop {{r4-r6,pc}};
        """

    code['labels']=f"""
            push {{r4-r8,lr}};{c('r4',fixed)} movs r5,#39;movs r6,#15;
            fixed_loop: ldr r0,[r4],#4;mov r1,r5;movs r2,#0;{native(0x103fbb5c)}
            adds r5,#1;subs r6,#1;bne fixed_loop;
            {call('get')} mov r4,r0;{c('r0',strings['off'])} cmp r4,#0;beq enabled;
            ldr r1,[r4,#{ENABLED}];cmp r1,#1;bne enabled;{c('r0',strings['on'])}
            enabled: movs r1,#54;movs r2,#0;{native(0x103fbb5c)}
            {c('r0',strings['none'])} cmp r4,#0;beq selected;
            ldr r1,[r4,#{LENGTH}];cmp r1,#0;beq selected;
            ldr r1,[r4,#{SELECTED}];cmp r1,#4;bhi selected;
            {c('r0',file_names)} ldr r0,[r0,r1,lsl #2];
            selected: movs r1,#55;movs r2,#0;{native(0x103fbb5c)}
            {c('r0',strings['empty'])} movs r1,#56;movs r2,#0;{native(0x103fbb5c)}
            {c('r0',strings['current'])} movs r1,#57;movs r2,#0;{native(0x103fbb5c)}
            movs r5,#0;
            switch_loop: {c('r0',strings['empty'])} cmp r4,#0;beq switch_put;
            ldr r1,[r4,#{ENABLED}];cmp r1,r5;bne switch_put;{c('r0',strings['current'])}
            switch_put: add r1,r5,#58;movs r2,#0;{native(0x103fbb5c)}
            adds r5,#1;cmp r5,#2;bne switch_loop;
            movs r5,#0;
            files_loop: {c('r0',strings['empty'])} cmp r4,#0;beq file_put;
            ldr r1,[r4,#{LENGTH}];cmp r1,#0;beq file_put;
            ldr r1,[r4,#{SELECTED}];cmp r1,r5;bne file_put;{c('r0',strings['current'])}
            file_put: add r1,r5,#60;movs r2,#0;{native(0x103fbb5c)}
            adds r5,#1;cmp r5,#5;bne files_loop;
            cmp r4,#0;beq done;ldr r1,[r4,#{RESULT}];cmp r1,#7;bhi done;cmp r1,#0;beq done;
            {c('r0',errors)} ldr r5,[r0,r1,lsl #2];mov r0,r5;movs r1,#40;movs r2,#0;{native(0x103fbb5c)}
            mov r0,r5;movs r1,#52;movs r2,#0;{native(0x103fbb5c)}
            mov r0,r5;movs r1,#53;movs r2,#0;{native(0x103fbb5c)}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['render']=f"""
            push {{r4-r8,lr}};mov r4,r0;{call('labels')}
            ldr r5,[r4,#36];cmp r5,#0;beq done;ldr r6,[r4,#40];movs r7,#0;
            next_row:ldrh r0,[r6,#20];cmp r7,r0;bhs header;
            mov r0,r5;movs r1,#1;mov r2,r7;{native(0x103fcf0e)} mov r8,r0;
            ldr r0,[r6,#12];movs r1,#28;mla r0,r7,r1,r0;ldr r1,[r0];mov r0,r8;{native(0x103fb344)}
            mov r0,r5;mov r1,r7;movs r2,#0;{native(0x103fcf5c)} mov r8,r0;
            ldr r0,[r6,#12];movs r1,#28;mla r0,r7,r1,r0;ldr r1,[r0,#20];mov r0,r8;{native(0x103fb344)}
            adds r7,#1;b next_row;
            header:ldr r1,[r4,#12];{c('r0',HEADER_OBJECT_ID)}{native(0x103faebe)}
            ldr r1,[r6,#8];ldr r1,[r1];{native(0x103fb344)}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['action']=f"""
            push {{r4-r8,lr}};mov r8,r0;{call('get')}mov r4,r0;cmp r4,#0;beq done;
            ldrh r5,[r8,#22];ldrh r0,[r8,#24];add r5,r5,r0;ldrh r6,[r8,#2];
            {c('r0',FILES_INDEX)}cmp r6,r0;beq file_apply;
            {c('r0',SWITCH_INDEX)}cmp r6,r0;beq switch_apply;
            cmp r5,#0;beq open_switch;cmp r5,#1;beq open_files;
            cmp r5,#2;beq preview;cmp r5,#3;bne redraw;
            ldr r0,[r4,#{LENGTH}];cmp r0,#0;beq reload;
            reload:ldr r0,[r4,#{SELECTED}];{call('load')}b redraw;
            open_switch:movs r0,#0;str r0,[r4,#{RESULT}];movs r0,#2;{c('r1',SWITCH_INDEX)}{native(0x10404c42)}
            {c('r0',SWITCH_OFFSET)}add r0,r4,r0;ldr r1,[r4,#{ENABLED}];{c('r2',switch_ids)}ldr r1,[r2,r1,lsl #2];{native(0x10402bc2)}b done;
            open_files:movs r0,#0;str r0,[r4,#{RESULT}];movs r0,#2;{c('r1',FILES_INDEX)}{native(0x10404c42)}
            {c('r0',FILES_OFFSET)}add r0,r4,r0;ldr r1,[r4,#{SELECTED}];{c('r2',file_ids)}ldr r1,[r2,r1,lsl #2];{native(0x10402bc2)}b done;
            file_apply:cmp r5,#4;bhi redraw;mov r0,r5;{call('load')}cmp r0,#1;bne redraw;
            movs r0,#0;str r0,[r4,#{RESULT}];b return_root;
            switch_apply:cmp r5,#1;bhi redraw;cmp r5,#0;beq disable;
            ldr r0,[r4,#{LENGTH}];cmp r0,#0;bne enabled;
            ldr r0,[r4,#{SELECTED}];{call('load')}cmp r0,#1;bne redraw;
            enabled:movs r0,#1;str r0,[r4,#{ENABLED}];b switched;
            disable:movs r0,#0;str r0,[r4,#{ENABLED}];str r0,[r4,#{PENDING}];
            switched:movs r0,#0;str r0,[r4,#{RESULT}];
            return_root:movs r0,#2;{c('r1',PAGE_INDEX)}{native(0x10404c42)}b draw;
            preview:ldr r0,[r4,#{LENGTH}];cmp r0,#0;beq no_file;
            ldr r0,[r4,#{BUSY}];cmp r0,#0;bne busy;
            {c('r0',0x138d)}{native(0x1016fef2)}cmp r0,#0;bne busy;
            {c('r0',0xa188d1a0)}ldrb r0,[r0];cmp r0,#0;bne busy;
            add r0,r4,#{BUSY};movs r1,#0;movs r2,#2;{call('cas')}cmp r0,#1;bne busy;
            mov r0,r4;{call('start')}b redraw;
            no_file:movs r0,#1;str r0,[r4,#{RESULT}];b redraw;
            busy:movs r0,#5;str r0,[r4,#{RESULT}];
            redraw:mov r0,r8;{call('render')}
            draw:{c('r0',0x103fa771)}movs r1,#3;movs r2,#7;{native(0x103fac5a)}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['getter']=f"""
            cmp r0,#2;bne original;{c('r12',PAGE_INDEX)}cmp r1,r12;blo original;
            {c('r12',SWITCH_INDEX)}cmp r1,r12;bhi original;
            push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            ldr r1,[sp];{c('r2',PAGE_INDEX)}cmp r1,r2;beq root;adds r2,#1;cmp r1,r2;beq files;
            {c('r2',SWITCH_OFFSET)}b add_page;
            files:{c('r2',FILES_OFFSET)}b add_page;
            root:movs r2,#{PAGE_OFFSET};add_page:add r0,r0,r2;
            ret:pop {{r1-r3,pc}};
            original:cmp r0,#7;mov r2,r0;mov.w r0,#0;bhs outside;{c('r0',0xa200da5c)}
            .balign 4;ldr.w pc,[pc,#0];.word 0x103fcccf;outside:bx lr;
        """

    code['route']=f"""
            push {{r0-r4,lr}};mov r4,r0;{call('get')}cmp r0,#0;beq original;
            add r2,r0,#{PAGE_OFFSET};cmp r4,r2;beq owned;
            {c('r2',FILES_OFFSET)}add r2,r0,r2;cmp r4,r2;beq owned;
            {c('r2',SWITCH_OFFSET)}add r2,r0,r2;cmp r4,r2;bne original;
            owned:ldr r3,[sp,#4];{c('r2',0x239)}cmp r3,r2;beq activate;
            {c('r2',0x23e)}cmp r3,r2;beq activate;
            {c('r2',0x252)}cmp r3,r2;beq activate;
            {c('r2',0x25f)}cmp r3,r2;bne original;
            activate:mov r0,r4;mov r1,r3;{call('action')}
            add sp,#4;pop {{r1-r4,lr}};{c('r0',0x3a0)}bx lr;
            original:pop {{r0-r4,lr}};push {{r4-r10,lr}};movs r5,r0;mov r6,r1;
            .balign 4;ldr.w pc,[pc,#0];.word 0x1048b403;
        """
    return code
