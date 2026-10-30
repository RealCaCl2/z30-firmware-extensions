# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Observe native queued lifecycle completion without doing file I/O there.

The SysC_Msg worker's callback binding is a checked stock pointer. Its original
callback finishes the native dispatcher, including releasing the SysC lock,
before we update readiness and request work on the separate audio worker.
The synchronous initialization dispatcher is deliberately left untouched.
"""
import struct

from ..assembly import constant, invoke
from .sound_auto_audio import (READY_FLAGS, WANT_PLAY, USER_EPOCH, MOUNTED,
                               APP_READY, LIFECYCLE_STEP)

SYSC_DISPATCH_POINTER = 0x100591A0
STOCK_SYSC_DISPATCH = 0x100590D8
CARD0_STATE = 0xC207BB61
CARD_MOUNTED_STATE = 4
FIRST_START_COMPLETE = 0x12
CARD_MOUNT_COMPLETE = 0x16

SIZES = dict(auto_lifecycle=512, auto_cancel=128)


def auto_lifecycle_code(f):
    c = constant
    call = lambda name: invoke(f[name])
    return {
        # Callers may be the serialized SysC task or the menu task. Increment
        # the epoch atomically; a concurrent UI operation invalidates a delayed
        # photograph request even if the camera becomes ready again quickly.
        'auto_cancel': f"""
            {c('r0',WANT_PLAY)}movs r1,#0;str r1,[r0];dmb ish;
            {c('r0',USER_EPOCH)}movs r3,#8;
            retry:ldrex r1,[r0];adds r1,#1;strex r2,r1,[r0];cmp r2,#0;beq done;
            subs r3,#1;bne retry;clrex;
            done:dmb ish;bx lr;
        """,
        'auto_lifecycle': f"""
            push {{r4-r8,lr}};mov r4,r0;
            cmp r4,#0;beq original_only;
            ldr r5,[r4,#16];ldr r6,[r4,#20];
            {invoke(STOCK_SYSC_DISPATCH)}mov r8,r0;
            {c('r4',READY_FLAGS)}
            cmp r5,#{FIRST_START_COMPLETE};beq application_ready;
            cmp r5,#7;beq invalidated;
            cmp r5,#8;beq request;
            cmp r6,#0;bne done;
            cmp r5,#{CARD_MOUNT_COMPLETE};beq card_ready;
            cmp r5,#0x1d;beq card_ready;
            cmp r5,#0x15;beq card_absent;
            cmp r5,#0x17;beq card_absent;
            cmp r5,#0x18;beq card_absent;
            cmp r5,#0x19;beq card_absent;
            cmp r5,#0x1c;beq card_absent;
            b done;
            application_ready:ldr r0,[r4];orr r0,r0,#{APP_READY};str r0,[r4];dmb ish;b request;
            card_ready:{c('r0',CARD0_STATE)}ldrb r0,[r0];cmp r0,#{CARD_MOUNTED_STATE};bne card_absent;
            ldr r0,[r4];orr r0,r0,#{MOUNTED};str r0,[r4];dmb ish;b request;
            card_absent:ldr r0,[r4];bic r0,r0,#{MOUNTED};str r0,[r4];
            invalidated:ldr r0,[r4];add r0,r0,#{LIFECYCLE_STEP};str r0,[r4];dmb ish;
            cancel:{call('auto_cancel')}b done;
            request:movs r0,#0;{call('auto_request')}
            done:mov r0,r8;pop {{r4-r8,pc}};
            original_only:pop {{r4-r8,lr}};{c('r12',STOCK_SYSC_DISPATCH|1)}bx r12;
        """,
    }


def install(plan, f):
    """Patch only the native queued-task binding and return audit metadata."""
    expected = struct.pack('<I', STOCK_SYSC_DISPATCH | 1)
    if plan.image.read(SYSC_DISPATCH_POINTER, 4) != expected:
        raise ValueError('Unexpected native SysC message callback binding')
    plan.edit(SYSC_DISPATCH_POINTER, struct.pack('<I', f['auto_lifecycle'] | 1),
              'Defer sound preload after native queued lifecycle completion')
    return dict(
        sysc_callback_pointer=hex(SYSC_DISPATCH_POINTER),
        original_sysc_callback=hex(STOCK_SYSC_DISPATCH | 1),
        first_application_start_command=FIRST_START_COMPLETE,
        card_mount_complete_command=CARD_MOUNT_COMPLETE,
        card_slot=0, native_card_state_address=hex(CARD0_STATE),
        native_card_mounted_state=CARD_MOUNTED_STATE,
        readiness_low_bits=dict(mounted=MOUNTED, application_started=APP_READY),
        lifecycle_generation_increment=LIFECYCLE_STEP,
        original_dispatcher_runs_first=True, file_io_in_sysc_callback=False,
        synchronous_initialization_dispatcher_modified=False,
        observed_command_source='Stock named SysC command table at 0x114a2688',
        ordering_evidence='FirstStartCompleteSyncHook (0x11) precedes queued FirstStartComplete (0x12); mount success is queued as 0x16',
        physical_startup_verified=False,
    )
