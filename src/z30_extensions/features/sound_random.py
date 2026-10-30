# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Bounded preloaded random sound bank for the opt-in shutter-random preset.

Only loading touches SD. Selection runs after obtaining playback ownership;
the selected pointer and length remain fixed through queued/playing/stopping.
Each clip retains the stock 207360-byte PCM limit. Four separate slots hold
up to 69120 input bytes (4.32 seconds total at 8 kHz mono), preserving the
existing menu objects between the original PCM slot and the three new slots.
"""
from .sound_layout import *
from .sound_random_layout import *
from .sound_auto_audio import QUEUED
from ..assembly import constant, invoke


def extend_audio_code(codes, addresses, path_table):
    c = constant
    call = lambda name: invoke(addresses[name])
    native = lambda address: invoke(address, thumb=address != 0x10199bdc)

    # Called by ensure before publishing/restoring settings. The enclosing
    # allocation was zeroed, so all bank descriptors start absent.
    codes['random_init'] = f"""
        {c('r1',RANDOM_META_OFFSET)} add r1,r0,r1; str r1,[r0,#{RANDOM_META_PTR}];
        {c('r2',RANDOM_SEED)} str r2,[r1,#{RNG}];
        mvn r2,#0; str r2,[r1,#{LAST}];
        {c('r2',PCM_OFFSET)} add r2,r0,r2; str r2,[r0,#{ACTIVE_PCM}]; bx lr;
    """

    # Caller owns BUSY=1. Returns validated raw PCM byte length or zero.
    # This helper only overwrites the separate WAV scratch area. It never
    # changes the published audio or selection, even on failed I/O/close.
    codes['random_read'] = f"""
        push {{r4-r8,lr}}; sub sp,#48; mov r4,r0; mov r7,r1;
        cmp r7,#4; bhi invalid;
        ldr r0,[r4,#112]; cmp r0,#0; blt fd_clear; {native(0x102c89ee)}
        cmp r0,#0; bne close_failed; mvn r0,#0; str r0,[r4,#112];
        fd_clear: {c('r0',QUEUED)} ldr r0,[r0]; cmp r0,#1; bne open_ready;
        {call('auto_ready')} cmp r0,#1; bne read_failed;
        open_ready: mov r0,sp; movs r1,#48; {native(0x10199c40)}
        mvn r0,#0; str r0,[sp,#8]; {c('r0',path_table)} ldr r1,[r0,r7,lsl #2];
        mov r0,sp; {native(0x1033829e)} mov r0,sp; movs r1,#0; movs r2,#0; {native(0x103382a2)}
        {c('r1',SUCCESS)} cmp r0,r1; bne open_failed;
        ldr r8,[sp,#8]; movs r0,#0; str r0,[sp,#40];
        mov r0,sp; add r1,r4,#{WAV_OFFSET}; {c('r2',17325)} add r3,sp,#40; {native(0x10338414)}
        mov r6,r0; mov r0,sp; {native(0x1033832e)} {c('r1',SUCCESS)} cmp r0,r1; beq closed;
        mov r0,r8; {native(0x102c89ee)} cmp r0,#0; beq closed;
        str r8,[r4,#112]; b close_failed;
        closed: {c('r1',SUCCESS)} cmp r6,r1; bne read_failed;
        add r0,r4,#{WAV_OFFSET}; ldr r1,[sp,#40]; {call('validate')} cmp r0,#0; beq invalid;
        movs r1,#0; str r1,[r4,#{RESULT}]; b done;
        open_failed: ldr r8,[sp,#8]; cmp r8,#0; blt missing;
        mov r0,sp; {native(0x1033832e)} {c('r1',SUCCESS)} cmp r0,r1; beq missing;
        str r8,[r4,#112]; b close_failed;
        missing: movs r5,#1; b failed;
        read_failed: movs r5,#2; b failed;
        invalid: movs r5,#3; b failed;
        close_failed: movs r5,#4;
        failed: str r5,[r4,#{RESULT}]; movs r0,#0;
        done: add sp,#48; pop {{r4-r8,pc}};
    """

    codes['load'] = f"""
        push {{r4-r10,lr}}; mov r7,r0; {call('get')} mov r4,r0;
        cmp r4,#0; beq absent; cmp r7,#{RANDOM_INDEX}; bhi invalid_selection;
        add r0,r4,#{BUSY}; movs r1,#0; movs r2,#1; {call('cas')} cmp r0,#1; bne busy;
        ldr r5,[r4,#{RANDOM_META_PTR}]; cmp r5,#0; beq invalid_meta;
        cmp r7,#{RANDOM_INDEX}; beq bank;

        mov r0,r4; mov r1,r7; {call('random_read')} cmp r0,#0; beq unlock_failure;
        mov r2,r0; add r0,r4,#{WAV_OFFSET+44}; {c('r1',PCM_OFFSET)} add r1,r4,r1;
        {call('convert')} str r0,[r4,#{LENGTH}];
        {c('r1',PCM_OFFSET)} add r1,r4,r1; str r1,[r4,#{ACTIVE_PCM}];
        movs r0,#0; str r0,[r5,#{COUNT}]; str r0,[r5,#{USED}];
        mvn r0,#0; str r0,[r5,#{LAST}]; b publish;

        bank: movs r0,#0; str r0,[r4,#{LENGTH}]; str r0,[r4,#{PENDING}];
        str r0,[r5,#{COUNT}]; str r0,[r5,#{USED}];
        mvn r0,#0; str r0,[r5,#{LAST}];
        movs r6,#1; movs r8,#0; movs r9,#0;
        next_file: mov r0,r4; mov r1,r6; {call('random_read')} cmp r0,#0; bne add_clip;
        ldr r0,[r4,#{RESULT}]; cmp r0,#1; beq next_slot; cmp r0,#3; beq next_slot;
        b bank_failure;
        add_clip: mov r10,r0; add r1,r8,r10; {c('r2',MAX_TOTAL_DATA)} cmp r1,r2; bhi too_large;
        {c('r1',PCM_CAPACITY)} mul r1,r9,r1; cmp r9,#0; beq slot_ready;
        {c('r2',RANDOM_SLOT_GAP)} add r1,r1,r2;
        slot_ready:
        add r0,r5,#{DESCRIPTORS}; add r0,r0,r9,lsl #3; str r1,[r0];
        {c('r2',PCM_OFFSET)} add r1,r1,r2; add r1,r1,r4;
        add r0,r4,#{WAV_OFFSET+44}; mov r2,r10; {call('convert')}
        add r1,r5,#{DESCRIPTORS}; add r1,r1,r9,lsl #3; str r0,[r1,#4];
        add r8,r8,r10; adds r9,#1;
        next_slot: adds r6,#1; cmp r6,#5; blo next_file;
        cmp r9,#0; beq no_files;
        str r9,[r5,#{COUNT}]; str r8,[r5,#{USED}];
        ldr r0,[r5,#{DESCRIPTORS+4}]; str r0,[r4,#{LENGTH}];
        {c('r0',PCM_OFFSET)} add r0,r4,r0; str r0,[r4,#{ACTIVE_PCM}];
        publish: str r7,[r4,#{SELECTED}]; movs r0,#0; str r0,[r4,#{RESULT}];
        dmb ish; str r0,[r4,#{BUSY}]; movs r0,#1; b done;

        too_large: movs r0,#3; str r0,[r4,#{RESULT}]; b bank_failure;
        no_files: movs r0,#1; str r0,[r4,#{RESULT}];
        bank_failure: movs r0,#0; str r0,[r5,#{COUNT}]; str r0,[r5,#{USED}];
        str r0,[r4,#{LENGTH}]; str r0,[r4,#{ENABLED}]; str r0,[r4,#{PENDING}];
        str r0,[r4,#{ACTIVE_PCM}]; b unlock_failure;
        invalid_meta: movs r0,#3; str r0,[r4,#{RESULT}];
        unlock_failure: movs r0,#0; dmb ish; str r0,[r4,#{BUSY}]; b done;
        invalid_selection: movs r0,#3; str r0,[r4,#{RESULT}]; b absent;
        busy: movs r0,#5; str r0,[r4,#{RESULT}];
        absent: movs r0,#0;
        done: pop {{r4-r10,pc}};
    """

    # At most one owner can enter this function: start is invoked after the
    # idle->queued CAS, or by the completion callback after prior playback.
    # LAST is a bank ordinal, not the user's persisted SELECTED preference.
    codes['random_select'] = f"""
        push {{r4-r8,lr}}; mov r4,r0;
        ldr r0,[r4,#{BUSY}]; cmp r0,#2; bne bad;
        ldr r0,[r4,#{SELECTED}]; cmp r0,#{RANDOM_INDEX}; bne fixed;
        ldr r5,[r4,#{RANDOM_META_PTR}]; cmp r5,#0; beq bad;
        ldr r6,[r5,#{COUNT}]; cmp r6,#1; blo bad; cmp r6,#4; bhi bad;
        ldr r7,[r5,#{RNG}]; cmp r7,#0; bne seeded; {c('r7',RANDOM_SEED)}
        seeded: eor r7,r7,r7,lsl #13; eor r7,r7,r7,lsr #17; eor r7,r7,r7,lsl #5;
        str r7,[r5,#{RNG}]; ldr r8,[r5,#{LAST}]; mov r1,r6;
        cmp r6,#1; beq single; cmp r8,r6; bhs choose;
        subs r1,#1;
        choose: udiv r0,r7,r1; mls r0,r0,r1,r7;
        cmp r8,r6; bhs chosen; cmp r0,r8; blo chosen; adds r0,#1; b chosen;
        single: movs r0,#0;
        chosen: str r0,[r5,#{LAST}]; add r1,r5,#{DESCRIPTORS}; add r1,r1,r0,lsl #3;
        ldr r2,[r1]; ldr r3,[r1,#4]; cmp r3,#0; beq bad;
        {c('r1',PCM_CAPACITY)} cmp r3,r1; bhi bad; mul r1,r0,r1;
        cmp r0,#0; beq expected_slot; {c('r8',RANDOM_SLOT_GAP)} add r1,r1,r8;
        expected_slot: cmp r2,r1; bne bad;
        {c('r1',PCM_OFFSET)} add r1,r1,r4; add r1,r1,r2;
        str r1,[r4,#{ACTIVE_PCM}]; str r3,[r4,#{LENGTH}]; dmb ish;
        good: movs r0,#1; pop {{r4-r8,pc}};
        fixed: cmp r0,#4; bhi bad; ldr r1,[r4,#{LENGTH}]; cmp r1,#0; beq bad;
        {c('r2',PCM_CAPACITY)} cmp r1,r2; bhi bad;
        {c('r1',PCM_OFFSET)} add r1,r1,r4; str r1,[r4,#{ACTIVE_PCM}]; b good;
        bad: movs r0,#0; pop {{r4-r8,pc}};
    """

    # Keep the native job creation/error cleanup, selecting a snapshot before
    # queueing it. Both direct trigger and pending continuation call start.
    prefix = 'push {r4-r8,lr}; mov r4,r0;'
    assert prefix in codes['start']
    codes['start'] = codes['start'].replace(prefix, prefix + f"\n        {call('random_select')} cmp r0,#1; bne failure;", 1)
    old_data_pointer = f"{c('r1',PCM_OFFSET)} add r1,r0,r1;"
    assert old_data_pointer in codes['data']
    codes['data'] = codes['data'].replace(old_data_pointer, f'ldr r1,[r0,#{ACTIVE_PCM}];', 1)

    # Public state remains hidden until initialization and deferred-worker
    # postvalidation finish. Cold capture performs only bounded queue work;
    # the native audio task owns all initialization, card I/O and conversion.
    codes['trigger'] = f"""
        push {{r4-r6,lr}}; {call('auto_get')} mov r4,r0; cmp r4,#0; beq cold;
        ldr r0,[r4,#{ENABLED}]; cmp r0,#1; bne no;
        ldr r0,[r4,#{LENGTH}]; cmp r0,#0; beq no;
        {c('r0',0x188c)} {native(0x101993de)} cmp r0,#0; bne no;
        {c('r0',0x57b)} {native(0x101993de)} cmp r0,#1; bne no;
        ldr r0,[r4,#{BUSY}]; cmp r0,#1; beq no;
        cmp r0,#0; bne pending;
        {c('r0',0xc1885a50)} ldrb r0,[r0]; cmp r0,#0; bne no;
        add r0,r4,#{BUSY}; movs r1,#0; movs r2,#2; {call('cas')} cmp r0,#1; bne no;
        mov r0,r4; {call('start')} b done;
        pending: cmp r0,#4; bhi no;
        movs r0,#1; str r0,[r4,#{PENDING}]; mov r0,r4; {call('stop')} b done;
        cold: {c('r0',0x188c)} {native(0x101993de)} cmp r0,#0; bne no;
        {c('r0',0x57b)} {native(0x101993de)} cmp r0,#1; bne no;
        {c('r0',0xc1885a50)} ldrb r0,[r0]; cmp r0,#0; bne no;
        movs r0,#1; {call('auto_request')} b done;
        no: movs r0,#0; done: pop {{r4-r6,pc}};
    """
    # Opening the menu cancels a delayed shutter sound. A racing menu prepare
    # may return a state while the worker still owns postvalidation; recheck
    # the public getter before exposing its page.
    codes['open'] = f"""
        push {{r4,lr}}; {call('auto_cancel')} {call('ensure')}
        {call('auto_get')} cmp r0,#0; beq done; {call('labels')}
        movs r0,#2; {c('r1',PAGE_INDEX)} {native(0x1045f732)}
        done: movs r0,#0; pop {{r4,pc}};
    """

    # Ignore late callbacks while idle/loading. In particular, an old device
    # completion must not release the exclusive preload lock (BUSY=1).
    complete_guard = "cmp r4,#0; beq done;"
    assert complete_guard in codes['complete']
    codes['complete'] = codes['complete'].replace(complete_guard,
        complete_guard + f" ldr r0,[r4,#{BUSY}]; cmp r0,#2; blo done; cmp r0,#4; bhi done;", 1)
    started = 'started: ldr r0,[r6,#4]; cmp r0,#0; bne done;'
    assert started in codes['complete']
    codes['complete'] = codes['complete'].replace(started,
        f'started: ldr r0,[r4,#{BUSY}]; cmp r0,#2; bne done; '
        'ldr r0,[r6,#4]; cmp r0,#0; bne failed;', 1)
