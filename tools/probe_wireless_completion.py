"""Trace stock Z30 acceptance/completion routing with offline ARM fixtures.

Native IPC, queues, sockets and timing remain synthetic; no camera access.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from probe_wireless_config import SOURCE, DESTINATION, SIZE, runtime


def inspect(data):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

    StockImage(data)
    embedded = data[SOURCE - BASE:SOURCE - BASE + SIZE]
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

    def read_rom(address, length):
        if SOURCE <= address < SOURCE + SIZE:
            offset = address - SOURCE
            assert offset + length <= SIZE
            return embedded[offset:offset + length]
        return data[address - BASE:address - BASE + length]

    def decode_rom(address, length):
        address_run = runtime(address) if SOURCE <= address < SOURCE + SIZE else address
        return [dict(address=hex(i.address), bytes=i.bytes.hex(),
                     mnemonic=i.mnemonic, operands=i.op_str)
                for i in md.disasm(read_rom(address, length), address_run)]

    # The network worker prepares its completion data and posts IPC group 4,
    # command 3 through the embedded service message wrapper.
    worker = decode_rom(0x11CB7E6E, 0x18)
    worker_text = [(i['mnemonic'], i['operands']) for i in worker]
    expected_worker = [
        ('movs', 'r3, #0'), ('movs', 'r1, #3'), ('movs', 'r0, #4'),
        ('add', 'r2, sp, #8'), ('bl', '#0xc1018876'),
    ]
    assert worker_text[:5] == expected_worker, worker_text

    # The wrapper copies four words from the caller's data and a fifth word
    # from its r3 argument into the queued message.
    wrapper = decode_rom(0x11CB6F3E, 0x1C)
    wrapper_text = [(i['mnemonic'], i['operands']) for i in wrapper]
    expected_copy = [
        ('ldm', 'r5!, {r0, r1, r2, r3}'),
        ('add', 'r5, sp, #0xc'),
        ('stm', 'r5!, {r0, r1, r2, r3, r6}'),
    ]
    assert wrapper_text[:3] == expected_copy, wrapper_text

    # This waiter consumes command 4 ACCEPTANCE, not command 3 completion.
    host = decode_rom(0x104836D2, 0x58)
    host_text = [(i['mnemonic'], i['operands']) for i in host]
    host_checks = {
        'submits_group4_command5': ('movs', 'r0, #4') in host_text and
                                   ('movs', 'r1, #5') in host_text,
        'matches_socket_at_message_plus_0x0c': ('ldrb', 'r0, [r1, #0xc]') in host_text and
                                                ('cmp', 'r0, r4') in host_text,
        'reads_return_word_at_message_plus_0x10': ('ldr', 'r5, [r1, #0x10]') in host_text,
    }
    assert all(host_checks.values()), host_text

    # Command 3 is also submitted by the host in the opposite direction.
    host_command3 = decode_rom(0x10483080, 0x1A)
    host3_text = [(i['mnemonic'], i['operands']) for i in host_command3]
    assert ('movs', 'r0, #4') in host3_text and ('movs', 'r1, #3') in host3_text

    image = StockImage(data)
    table = image.words(0x104833A8, 1)[0]
    handlers = image.words(table, 5)
    assert table == 0xC180FEB0
    assert handlers == (0x1048314D, 0x10483165, 0x1048317D,
                        0x10483199, 0x104831BF)
    assert image.words(0x104833AC, 1)[0] == 0x10482FF7
    assert image.words(0x10483394, 1)[0] == 0xC1D80C44
    assert image.words(0x1048374C, 1)[0] == 0xC1D80C44
    assert image.read(0x11CB6B58, len(b'Booted ARM926 Firmware')) == b'Booted ARM926 Firmware'
    acceptance = decode_rom(0x11CB7D7E, 0x14)
    assert [(i['mnemonic'], i['operands']) for i in acceptance][-1] == (
        'bl', '#0xc1018876')

    return {
        'stock_eg_sha256': sha256(data),
        'worker_completion': {'rom_start': hex(0x11CB7E6E), 'instructions': worker,
                              'group': 4, 'command': 3, 'data_pointer': 'sp+8',
                              'auxiliary_argument_r3': 0},
        'message_wrapper': {'rom_start': hex(0x11CB6F3E), 'instructions': wrapper,
                            'copies_caller_words': 4, 'fifth_word_source': 'wrapper r3'},
        'host_waiter': {'rom_start': hex(0x104836D2), 'instructions': host,
                        'checks': host_checks, 'queue_message_offsets': {
                            'socket_match': '0x0C', 'returned_word': '0x10'}},
        'host_command3_use': {'rom_start': hex(0x10483080),
                              'instructions': host_command3,
                              'group': 4, 'command': 3},
        'host_group4_notifications': {
            'registration': '0x10483020..0x10483024',
            'dispatcher': '0x10482FF6', 'table': hex(table),
            'handlers': [hex(h) for h in handlers],
            'command3': '0x10483198: forwards completion to installed callback index 3; suppresses returned bytes if success is zero',
            'command4': '0x104831BE: queues socket and accepted/requested length; signals event 1',
            'queue_object': '0xC1D80CA0', 'event_object': '0xC1D80C44',
        },
        'worker_acceptance_before_loop': acceptance,
        'worker_payload_fields': {
            'command4': ['socket index (byte)', 'requested block length', 'zero', 'zero'],
            'command3': ['socket index (byte)', 'registered original buffer',
                         'socket-returned total or negative error', 'full-length success flag'],
            'command3_host_forwarding': 'When full-length success is zero, the host callback sees zero instead of the raw returned total/error.',
        },
        'service_isa_evidence': {
            'boot_string_rom': '0x11CB6B58',
            'boot_string': 'Booted ARM926 Firmware',
            'limitation': 'Main-program Thumb-2 MOVW/MOVT/LDR.W trampolines cannot be assumed valid for this ARM926 service. ARM or Thumb-1 stubs and owned executable placement must be checked separately.',
        },
        'interpretation': [
            'Group4/command4 acknowledges a block BEFORE socket queries/sends. The synchronous host waiter consumes this acceptance.',
            'Group4/command3 reports AFTER the send loop and enters a separate callback, not the waiter queue.',
            'The host command3 request is a different direction from service command3 notifications.',
            'The wrapper fifth word controls synchronous reply waiting; it is not a spare diagnostic word.',
            'Command4 payload words 2 and 3 are zero in stock and ignored by its current host handler; this is a candidate sideband, not validated firmware.',
            'Host waiter repeats the event wait, not the IPC request, when its queue has no matching item.',
            'This does not establish real IPC transport, all external consumers, timing units, or camera runtime behavior.',
            'No firmware was patched or executed on camera.',
        ],
    }


def exercise_notifications(data):
    """Run stock worker + stock host callbacks, bridged by explicit IPC stubs."""
    from native_runtime import Context, RAM
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_PC,
                                   UC_ARM_REG_LR)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
    rows = []
    cases = (
        ('room_for_all', [65536], 64240, None),
        ('small_available_chunks', [4096], 16384, None),
        ('one_empty_poll_then_room', [0, 65536], 64240, None),
        ('two_empty_polls_then_chunks', [0, 0, 4096], 16384, None),
        ('partial_socket_send', [8192], 16384, 4096),
    )
    for name, spaces, length, send_limit in cases:
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
        u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
        u.mem_map(0xC131D000, 0x11000)
        u.mem_map(0x50000000, 0x20000)
        u.mem_map(0x60000000, 0x10000)
        stack, buffer, record, message = 0x60008000, 0x50001000, 0xC131DF50, 0x50000100
        def write32(a, v):
            u.mem_write(a, struct.pack('<I', v))
        write32(record, 1)  # Synthetic active descriptor 1.
        write32(record + 8, 3)  # External socket index.
        write32(record + 12, buffer)  # Registered original buffer.
        write32(message + 12, 1)
        write32(message + 16, buffer)
        write32(message + 20, length)
        u.reg_write(UC_ARM_REG_SP, stack)
        u.reg_write(UC_ARM_REG_R1, message)
        events, notifications, sends, polls = [], [], [], []
        stop = runtime(0x11CB7D42)
        def hook(emulator, address, size, context):
            if address == stop:
                emulator.emu_stop()
                return
            a = [emulator.reg_read(r) for r in regs]
            if address == runtime(0x11CB6F12):
                payload = list(struct.unpack('<4I', emulator.mem_read(a[2], 16)))
                notifications.append(dict(group=a[0], command=a[1], payload=payload,
                                          synchronous_reply=a[3]))
                events.append(f'notification_{a[1]}')
                result = 0
            elif address == runtime(0x11D32012):
                space = spaces[min(len(polls), len(spaces)-1)]
                write32(a[3], space)
                polls.append(space)
                events.append('space_query')
                result = 0
            elif address == runtime(0x11D31B60):
                result = min(a[2], send_limit) if send_limit else a[2]
                sends.append(dict(requested=a[2], returned=result))
                events.append('socket_send')
            elif address == runtime(0x11D44634):
                assert a[0] == 10
                events.append('zero_space_delay')
                result = 0
            else:
                result = 0  # Explicit queue release/lock/unlock stubs.
            emulator.reg_write(UC_ARM_REG_R0, result)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
        for address in [stop] + [runtime(a) for a in (
            0x11CB6858, 0x11CB6F12, 0x11D32012, 0x11D31B60,
            0x11D44634, 0x11D418C8, 0x11D42044)]:
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        u.emu_start(runtime(0x11CB7D4C) | 1, stop + 2, count=10000)
        assert u.reg_read(UC_ARM_REG_PC) == stop
        assert u.reg_read(UC_ARM_REG_SP) == stack
        actual = sum(s['returned'] for s in sends)
        success = int(actual > 0 and actual == length)
        assert notifications == [
            dict(group=4, command=4, payload=[3, length, 0, 0], synchronous_reply=0),
            dict(group=4, command=3, payload=[3, buffer, actual, success], synchronous_reply=0),
        ], notifications
        assert events[0] == 'notification_4' and events[-1] == 'notification_3'

        # Real host registration + group dispatcher + both handlers. Only
        # native queues/events/IPC and the application callback are stubbed.
        host = Context(data, data)
        host.map(0xC1D80000, 0x10000)
        host.map(0xC1D3C000, 0x1000)
        payload, queued, callback = RAM + 0x100, RAM + 0x200, RAM + 0x400
        queue_object, event_object = 0xC1D80CA0, 0xC1D80C44
        host.u.mem_write(callback, bytes.fromhex('7047'))
        host.u.mem_write(event_object - 16 + 3*4, struct.pack('<I', callback | 1))
        queue_events, completed, requests = [], [], []
        def host_hook(emulator, address, size, context):
            a = [emulator.reg_read(r) for r in regs]
            if address == callback:
                assert a[0] == 3
                raw = bytes(emulator.mem_read(a[1], 16))
                completed.append([raw[0], *struct.unpack_from('<3I', raw, 4)])
                result = 0
            elif address == 0x10218AB0:
                assert a[0] == queue_object
                result = queued
            elif address == 0x10218B58:
                assert a[:2] == [queue_object, queued]
                raw = bytes(emulator.mem_read(queued + 12, 8))
                queue_events.append([raw[0], struct.unpack_from('<I', raw, 4)[0]])
                result = 0
            elif address == 0x10199E04:
                assert a[:2] == [event_object, 1]
                result = 0
            elif address == 0x10199E12:
                assert a[:2] == [event_object, 1]
                emulator.mem_write(a[2], struct.pack('<I', 1))
                result = 0
            elif address == 0x10218D88:
                assert a[0] == queue_object
                result = queued
            elif address == 0x10476C7C:
                requests.append(dict(group=a[0], command=a[1], mode=a[3],
                    payload=list(struct.unpack('<4I', emulator.mem_read(a[2], 16)))))
                result = 0
            else:
                result = 0  # Native queue init/release, event clear, cache.
            emulator.reg_write(UC_ARM_REG_R0, result)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
        for address in (callback, 0x10218994, 0x10218AB0, 0x10218B58,
                        0x10199E04, 0x10199E12, 0x10218D88, 0x10218D02,
                        0x10199E0A, 0x10204B0C, 0x10476C7C):
            host.u.hook_add(UC_HOOK_CODE, host_hook, begin=address, end=address)
        host.call(0x10483000)
        registry = struct.unpack('<I', host.u.mem_read(0xC1D3C288 + 0x18 + 4*4, 4))[0]
        assert registry == 0x10482FF7
        for notification in notifications:
            host.u.mem_write(payload, struct.pack('<4I', *notification['payload']))
            assert host.call(registry, (notification['command'], payload)) == 0
        assert queue_events == [[3, length]]
        assert completed == [[3, buffer, actual if success else 0, success]]
        returned = host.call(0x104836B6, (3, buffer, length))
        assert returned == length
        assert requests == [dict(group=4, command=5, mode=0, payload=[3, buffer, length, 0])]
        # A poison check proves the current stock command4 handler ignores its
        # two trailing words. This does not prove a modified IPC ABI on camera.
        host.u.mem_write(payload, struct.pack('<4I', 3, length, 0xDEADBEEF, 0x12345678))
        assert host.call(registry, (4, payload)) == 0
        assert queue_events[-1] == [3, length]
        rows.append(dict(name=name, events=events, notifications=notifications,
            host_acceptance_queue=queue_events[0], host_completion_callback=completed[0],
            waiter_returned=returned, socket_returned_total=actual,
            trailing_acceptance_words_ignored=True, stack_and_saved_registers_preserved=True))
    return dict(cases=rows, scope=(
        'Stock ARM instructions in worker and host registration/dispatch/handlers/waiter; '
        'two separate Unicorn instances bridged by captured four-word payloads. '
        'Queues, IPC transport, events, socket, delay, locks, cache and application callback are stubs. '
        'No physical timing, IPC transport, throughput or firmware patch validation.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a fresh path')
    data = extract_eg(args.source.read_bytes())
    result = inspect(data)
    result['offline_notifications'] = exercise_notifications(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('Verified stock notification routing and five offline worker/host scenarios; no camera validation')


if __name__ == '__main__':
    main()
