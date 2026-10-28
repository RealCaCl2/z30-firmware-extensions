"""Offline execution of the stock queued-send loop under synthetic backpressure.

Starts at an internal loop boundary with explicit fixture state. Socket status,
send, sleep, locks and unlocks are stubs. No network or physical camera access.
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


def probe_host_dispatch(data, stock=None):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_LR, UC_ARM_REG_PC)
    sizing = Context(stock or data, data)
    sizing.map(0xC1F7E000, 0x1000)  # Explicit zero-initialized header-size cache.
    packet_sizes = {'capacity': sizing.call(0x10339EAC, (), limit=1000),
                    'data_header': sizing.call(0x10339D4E, (10,), limit=10000),
                    'payload_bound': sizing.call(0x1031E7C2, (), limit=10000)}
    assert packet_sizes == {'capacity': 64240, 'data_header': 12, 'payload_bound': 64228}
    results = []
    for length in (4096, 64240):
        context = Context(stock or data, data)
        u = context.u
        context.map(0xC2A0A000, 0x2000)
        request, buffer, response = RAM + 0x100, RAM + 0x1000, RAM + 0x200
        def write32(address, value):
            u.mem_write(address, struct.pack('<I', value))
        # Explicit synthetic transport selection and completion message.
        write32(0xC1811244, request)  # PTPIP context + 0x5C, channel zero.
        u.mem_write(0xC1811240, b'\x03')  # Synthetic socket kind/index.
        write32(0xC1811A84, 0xC2A0AF24)
        write32(0xC2A0AF2C, 0x103B6993)
        write32(response + 0xC, 3)
        write32(response + 0x10, length)
        calls = []
        visited = []
        def hook(emulator, address, size, context_arg):
            if address in (0x1039C3AC, 0x103B6992, 0x104830C4, 0x104836B6):
                visited.append(hex(address))
                return
            regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
            r0, r1, r2, r3 = [emulator.reg_read(r) for r in regs]
            if address == 0x10476C7C:
                payload = struct.unpack('<4I', emulator.mem_read(r2, 16))
                calls.append({'group': r0, 'command': r1, 'mode': r3,
                              'socket_index': payload[0], 'buffer': hex(payload[1]),
                              'length': payload[2]})
                result = 0
            elif address == 0x10199E12:
                write32(r2, 1)
                result = 0
            elif address == 0x10218D88:
                result = response
            elif address in (0x10204B0C, 0x10218D02, 0x10199E0A):
                result = 0
            else:
                return
            emulator.reg_write(UC_ARM_REG_R0, result)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
        hooks = (0x1039C3AC, 0x103B6992, 0x104830C4, 0x104836B6,
                 0x10476C7C, 0x10199E12, 0x10218D88, 0x10204B0C,
                 0x10218D02, 0x10199E0A)
        for address in hooks:
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        returned = context.call(0x1031EAB0, (buffer, length, 0), limit=10000)
        assert returned == 1
        assert len(calls) == 1 and calls[0]['group'] == 4 and calls[0]['command'] == 5
        assert calls[0]['length'] == length and calls[0]['socket_index'] == 3
        assert calls[0]['buffer'] == hex(buffer)
        assert visited == ['0x1039c3ac', '0x103b6992', '0x104830c4', '0x104836b6']
        results.append({'input_length': length, 'visited': visited,
                        'request': calls[0], 'returned': returned})
    return {'cases': results, 'packet_sizes': packet_sizes,
            'scope': 'Explicit selected Wi-Fi vtable; cache, IPC, event and completion list are stubbed.'}


def probe_options(data, expected_send_buffer=65536, send_buffer_status=0):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R6,
                                   UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
    u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
    u.mem_map(0x60000000, 0x10000)
    u.reg_write(UC_ARM_REG_SP, 0x60008000)
    u.reg_write(UC_ARM_REG_R4, 1)
    u.reg_write(UC_ARM_REG_R6, 0xC11FFF80)
    u.mem_write(0xC11FFF80, b'\x20\x00')
    options = []
    stop = runtime(0x11CB806A)
    def hook(emulator, address, size, context):
        if address == stop:
            emulator.emu_stop()
            return
        if address == runtime(0x11D3195A):  # Synthetic accepted socket.
            result = 2
        elif address == runtime(0x11CB8A44):  # Synthetic valid connection.
            result = 1
        elif address == runtime(0x11D32400):
            regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
            socket, level, option, pointer = [emulator.reg_read(r) for r in regs]
            length = struct.unpack('<I', emulator.mem_read(emulator.reg_read(UC_ARM_REG_SP), 4))[0]
            value = bytes(emulator.mem_read(pointer, length))
            result = send_buffer_status if (level, option) == (0xFFFF, 0x1001) else 0
            options.append({'socket': socket, 'level': level, 'option': option,
                            'value_hex': value.hex(), 'length': length,
                            'synthetic_status': result})
        else:
            return
        emulator.reg_write(UC_ARM_REG_R0, result)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
    for address in (stop, runtime(0x11D3195A), runtime(0x11CB8A44), runtime(0x11D32400)):
        u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
    u.emu_start(runtime(0x11CB7FB6) | 1, stop + 2, count=10000)
    assert u.reg_read(UC_ARM_REG_PC) == stop
    assert [(o['level'], o['option']) for o in options] == [
        (0xFFFF, 0x1005), (0xFFFF, 0x1001), (6, 1), (0xFFFF, 0x80)]
    assert options[1]['value_hex'] == struct.pack('<I', expected_send_buffer).hex()
    assert options[2]['value_hex'] == struct.pack('<I', 1).hex()
    return {'options': options,
            'reached_setup_continuation': True,
            'scope': 'Stock accepted-socket setup; accept, validation and setsockopt are stubs.'}


def probe(data):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                   UC_ARM_REG_R6, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC)
    StockImage(data)
    cases = (
        ('room_for_all', [65536], 64240, None),
        ('small_available_chunks', [4096], 16384, None),
        ('one_empty_poll_then_room', [0, 65536], 64240, None),
        ('two_empty_polls_then_chunks', [0, 0, 4096], 16384, None),
        ('partial_socket_send', [8192], 16384, 4096),
    )
    results = []
    for name, spaces, length, send_limit in cases:
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(DESTINATION, (SIZE + 4095) & ~4095)
        u.mem_write(DESTINATION, data[SOURCE - BASE:SOURCE - BASE + SIZE])
        u.mem_map(0xC131D000, 0x11000)
        u.mem_map(0x50000000, 0x20000)
        u.mem_map(0x60000000, 0x10000)
        stack, buffer, record = 0x60008000, 0x50000000, 0xC131DF50
        def write32(address, value):
            u.mem_write(address, struct.pack('<I', value))
        def read32(address):
            return struct.unpack('<I', u.mem_read(address, 4))[0]
        write32(record, 1)  # Synthetic active socket; descriptor 1 => record offset 0.
        write32(stack + 8, buffer)
        write32(stack + 0xC, 0)
        write32(stack + 0x10, 1)
        write32(stack + 0x18, 0)
        write32(stack + 0x54, 4)
        for register, value in ((UC_ARM_REG_SP, stack), (UC_ARM_REG_R4, length),
                                (UC_ARM_REG_R5, 0), (UC_ARM_REG_R6, record)):
            u.reg_write(register, value)
        events, sends, sleeps, polls = [], [], [], []
        registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
        stop = runtime(0x11CB7E50)
        def hook(emulator, address, size, context):
            if address == stop:
                emulator.emu_stop()
                return
            args = [emulator.reg_read(r) for r in registers]
            if address == runtime(0x11D32012):
                assert args[:3] == [1, 0xFFFF, 0x100B]
                space = spaces[min(len(polls), len(spaces) - 1)]
                write32(args[3], space)
                polls.append(space)
                events.append({'poll_space': space})
                result = 0
            elif address == runtime(0x11D31B60):
                assert args[0] == 1
                result = min(args[2], send_limit) if send_limit else args[2]
                sends.append({'buffer_offset': args[1] - buffer,
                              'requested': args[2], 'returned': result})
                events.append({'send_requested': args[2], 'send_returned': result})
            elif address == runtime(0x11D44634):
                sleeps.append(args[0])
                events.append({'delay_argument': args[0]})
                result = 0
            elif address in (runtime(0x11D418C8), runtime(0x11D42044)):
                result = 0
            else:
                return
            emulator.reg_write(UC_ARM_REG_R0, result)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
        for address in [stop] + [runtime(a) for a in (
                0x11D32012, 0x11D31B60, 0x11D44634, 0x11D418C8, 0x11D42044)]:
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        u.emu_start(runtime(0x11CB7D98) | 1, stop + 2, count=10000)
        assert u.reg_read(UC_ARM_REG_PC) == stop
        assert u.reg_read(UC_ARM_REG_SP) == stack
        assert u.reg_read(UC_ARM_REG_R4) == 0
        assert all(delay == 10 for delay in sleeps)
        assert len(sleeps) == sum(space == 0 for space in spaces)
        assert sum(send['requested'] for send in sends) == length
        assert read32(stack + 0xC) == sum(send['returned'] for send in sends)
        results.append({'name': name, 'input_length': length, 'events': events,
                        'send_calls': sends, 'delay_calls': sleeps,
                        'reported_sent': read32(stack + 0xC),
                        'buffer_advanced': read32(stack + 8) - buffer})
    return {'stock_eg_sha256': sha256(data), 'cases': results,
            'accepted_socket_setup': probe_options(data),
            'host_dispatch': probe_host_dispatch(data),
            'limitations': [
                'Starts inside a worker, with synthetic state; no natural initialization.',
                'Socket query/send, delay and synchronization calls are stubbed.',
                'Option 0x100B meaning inferred from how stock code consumes its value.',
                'Delay argument 10 is observed; its physical time unit is not established.',
                'No timing, throughput, real socket partial-send behavior or hardware validated.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"Passed {len(report['cases'])} send-loop scenarios, "
          f"{len(report['host_dispatch']['cases'])} PTP/IP dispatch cases and socket setup capture")


if __name__ == '__main__':
    main()
