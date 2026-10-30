# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Bounded request count for initialization on the native audio mailbox task.

The audio worker's registration literal at 0x101dbb28 holds 0x101db77d. Our
replacement intercepts an exact event/envelope/callback triple; other messages
retain their original register arguments and handler. The native 0x10096c86
wrapper calls envelope[1](event, envelope), then frees the 28-byte message.
Our immutable envelope is never freed. Native FILSV1/FILSV2 tasks handle file
requests; these operations can wait inside firmware and have no known timeout.
"""
from .sound_layout import STATE_ANCHOR, ENABLED, LENGTH, PENDING
from .sound_random_layout import ACTIVE_PCM
from ..assembly import constant, invoke

READY_FLAGS = STATE_ANCHOR + 24
QUEUED = STATE_ANCHOR + 28
WANT_PLAY = STATE_ANCHOR + 32  # 0 or USER_EPOCH + 1, never a boolean.
ATTEMPTS = STATE_ANCHOR + 36
USER_EPOCH = STATE_ANCHOR + 40
MOUNTED, APP_READY = 1, 2
READY = READY_MASK = MOUNTED | APP_READY
LIFECYCLE_STEP = 0x100
MAX_ATTEMPTS = 2
AUDIO_QUEUE_PTR = 0xC1885A4C
AUDIO_QUEUE_OBJECT = 0xC21B8EC0
AUDIO_DISPATCH_POINTER = 0x101DBB28
STOCK_AUDIO_DISPATCH = 0x101DB77C
AUTO_EVENT = 0x574C4131
POWER_BASE = 0xC207BAC8
CARD_STATE = 0xC207BB61
CARD_FLAGS = 0xC2084BE0

SIZES = dict(auto_get=160, auto_ready=192, auto_request=512,
             auto_mark_play=256, auto_enqueue=256, auto_worker=512,
             auto_dispatch=256)


def auto_audio_code(f, envelope):
    """Emit routines; builder supplies immutable [0, auto_worker|1, 0].

    auto_request(r0=0 startup, 1 cold-shot) submits at most two native messages
    per boot. Cold requests during loading coalesce into one epoch-tagged play
    request; menu interaction cancels that token. The worker's ensure returns
    r1=1 only when that call created/restored a new state; cancellation must
    never disable a state another caller initialized. Public auto_get hides
    both initialization and worker postvalidation; private get serves loaders.
    """
    c = constant
    call = lambda name: invoke(f[name])
    n = invoke
    return {
        'auto_get': f"""
            push {{r4,lr}};
            {c('r0',STATE_ANCHOR+20)}ldr r0,[r0];cmp r0,#0;bne absent;
            {c('r0',QUEUED)}ldr r0,[r0];cmp r0,#0;bne absent;
            dmb ish;{call('get')}mov r4,r0;dmb ish;
            {c('r0',STATE_ANCHOR+20)}ldr r0,[r0];cmp r0,#0;bne absent;
            {c('r0',QUEUED)}ldr r0,[r0];cmp r0,#0;bne absent;
            mov r0,r4;pop {{r4,pc}};
            absent:movs r0,#0;pop {{r4,pc}};
        """,
        'auto_ready': f"""
            {c('r0',READY_FLAGS)}ldr r0,[r0];and r0,r0,#{READY_MASK};cmp r0,#{READY};bne no;
            {c('r0',POWER_BASE)}ldrb r1,[r0,#4];cmp r1,#1;bne no;
            ldrb r1,[r0,#40];cmp r1,#0;bne no;ldrb r1,[r0,#64];cmp r1,#0;bne no;
            {c('r0',CARD_STATE)}ldrb r0,[r0];cmp r0,#4;bne no;
            {c('r0',CARD_FLAGS)}ldrb r0,[r0];tst r0,#2;bne no;
            {c('r0',AUDIO_QUEUE_PTR)}ldr r0,[r0];{c('r1',AUDIO_QUEUE_OBJECT)}cmp r0,r1;bne no;
            ldr r0,[r0,#44];{c('r1',f['auto_dispatch']|1)}cmp r0,r1;bne no;
            movs r0,#1;bx lr;no:movs r0,#0;bx lr;
        """,
        'auto_mark_play': f"""
            push {{r4-r6,lr}};{c('r4',WANT_PLAY)}{c('r5',USER_EPOCH)}
            {c('r0',QUEUED)}ldr r0,[r0];cmp r0,#1;bne no;
            ldr r6,[r5];adds r6,#1;beq no;
            mov r0,r4;movs r1,#0;mov r2,r6;{call('cas')}cmp r0,#1;bne no;
            ldr r0,[r5];adds r0,#1;cmp r0,r6;bne stale;
            {c('r0',QUEUED)}ldr r0,[r0];cmp r0,#1;beq done;
            stale:mov r0,r4;mov r1,r6;movs r2,#0;{call('cas')}
            no:movs r0,#0;done:pop {{r4-r6,pc}};
        """,
        'auto_enqueue': f"""
            push {{r4-r6,lr}};movs r0,#28;movs r1,#0;{n(0x10211f4c)}mov r4,r0;cmp r4,#0;beq no;
            movs r0,#0;str r0,[r4];str r0,[r4,#4];str r0,[r4,#8];
            {c('r0',0x10096c87)}str r0,[r4,#12];{c('r0',AUTO_EVENT)}str r0,[r4,#16];
            {c('r0',envelope)}str r0,[r4,#20];str r4,[r4,#24];
            {c('r0',AUDIO_QUEUE_OBJECT)}mov r1,r4;{n(0x1021157c)}cmp r0,#0;beq good;
            mov r0,r4;{n(0x10211f5e)}
            no:movs r0,#0;pop {{r4-r6,pc}};
            good:movs r0,#1;pop {{r4-r6,pc}};
        """,
        'auto_request': f"""
            push {{r4-r8,lr}};mov r4,r0;{call('auto_ready')}cmp r0,#1;bne no;
            {call('auto_get')}cmp r0,#0;bne no;
            {c('r0',QUEUED)}movs r1,#0;movs r2,#1;{call('cas')}cmp r0,#1;bne coalesce;
            {call('auto_ready')}cmp r0,#1;bne release;
            {c('r6',ATTEMPTS)}ldr r0,[r6];cmp r0,#{MAX_ATTEMPTS};bhs release;
            adds r0,#1;str r0,[r6];
            cmp r4,#1;bne submit;{call('auto_mark_play')}
            submit:dmb ish;{call('auto_enqueue')}cmp r0,#1;beq done;
            release:{c('r0',WANT_PLAY)}movs r1,#0;str r1,[r0];
            dmb ish;{c('r0',QUEUED)}str r1,[r0];b no;
            coalesce:cmp r4,#1;bne no;{call('auto_mark_play')}
            no:movs r0,#0;done:pop {{r4-r8,pc}};
        """,
        'auto_worker': f"""
            push {{r4-r8,lr}};movs r4,#0;movs r7,#0;
            {call('auto_ready')}cmp r0,#1;bne finish;
            {c('r0',READY_FLAGS)}ldr r5,[r0];{c('r0',USER_EPOCH)}ldr r6,[r0];
            {call('ensure')}mov r4,r0;mov r7,r1;cmp r4,#0;beq finish;
            {call('auto_ready')}cmp r0,#1;bne invalid;
            {c('r0',READY_FLAGS)}ldr r0,[r0];cmp r0,r5;bne invalid;
            {c('r0',USER_EPOCH)}ldr r0,[r0];cmp r0,r6;bne finish;
            adds r6,#1;beq finish;
            {c('r0',WANT_PLAY)}mov r1,r6;movs r2,#0;{call('cas')}cmp r0,#1;bne finish;
            movs r8,#1;b release;
            invalid:cmp r7,#1;bne finish;movs r0,#0;
            str r0,[r4,#{ENABLED}];str r0,[r4,#{LENGTH}];str r0,[r4,#{PENDING}];str r0,[r4,#{ACTIVE_PCM}];
            finish:movs r8,#0;
            release:{c('r0',WANT_PLAY)}movs r1,#0;str r1,[r0];
            dmb ish;{c('r0',QUEUED)}str r1,[r0];
            cmp r8,#1;bne done;{call('auto_ready')}cmp r0,#1;bne done;
            {c('r0',USER_EPOCH)}ldr r0,[r0];adds r0,#1;cmp r0,r6;bne done;
            {call('trigger')}
            done:movs r0,#1;pop {{r4-r8,pc}};
        """,
        'auto_dispatch': f"""
            push {{r0-r3,r12,lr}};cmp r0,#0;beq stock;
            ldr r1,[r0,#16];{c('r2',AUTO_EVENT)}cmp r1,r2;bne stock;
            ldr r1,[r0,#20];{c('r2',envelope)}cmp r1,r2;bne stock;
            ldr r1,[r0,#12];{c('r2',0x10096c87)}cmp r1,r2;bne stock;
            pop {{r0-r3,r12,lr}};mov r1,r0;{c('r0',AUTO_EVENT)}{c('r12',0x10096c87)}bx r12;
            stock:pop {{r0-r3,r12,lr}};{c('r12',STOCK_AUDIO_DISPATCH|1)}bx r12;
        """,
    }
