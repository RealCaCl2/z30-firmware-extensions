"""Offline audit of stock USB service entry/exit; never touches a camera.

Runs stock dispatch, entry/exit state writes and async-result validation.
Hardware, settings refresh/write and resource cleanup services are fixtures.
Failure cases describe the stock wrapper contract, not camera recovery proof.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256


def backend_route(data):
    """Execute stock event dispatch through the registered transport builder."""
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC, UC_ARM_REG_LR)
    cases = []
    for mode in (0, 1):
        context = Context(data, data)
        u = context.u
        context.map(0xC18E5000, 0x1000)
        context.map(0xC18C6000, 0x2000)
        u.mem_write(0xC18C7018, struct.pack('<I', 0x115708BC))
        context.call(0x101F0B8C, (0xC18E5450,), limit=100)
        context.call(0x1027CCE6, (0x1005930F, 0x115709AC), limit=100)
        u.mem_write(0xC18E5398, bytes((mode,)))
        packets = []

        def hook(emulator, address, size, unused):
            if address == 0x100594A8:
                service = emulator.reg_read(UC_ARM_REG_R0)
                descriptor = emulator.reg_read(UC_ARM_REG_R1)
                pointer, length, channel, kind = struct.unpack('<4I', emulator.mem_read(descriptor, 16))
                raw = bytes(emulator.mem_read(pointer, length))
                packets.append(dict(service=service, length=length, channel=channel,
                                    kind=kind, data_hex=raw.hex()))
                assert service == 15 and length == 8 and channel == 1 and kind == 9
                assert raw[0] == 9 and raw[4] == mode
                returned = 1
            else:
                returned = 0  # Transport flush: no pending packets in fixture.
            emulator.reg_write(UC_ARM_REG_R0, returned)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

        for address in (0x100594A8, 0x10059442):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        context.call(0x101F087A, (0x40,), limit=5000)
        assert len(packets) == 1
        cases.append(dict(input_mode=mode, event_bits='0x40', packets=packets))
    return dict(cases=cases,
                binding='101F0B8C -> C18E5450; 1027CCE6 -> 1005930F / 115709AC',
                boundary='Stopped at original transport segmentation entry 100594A8; no I/O, receiver or reply modeled')


def probe(data):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_PC, UC_ARM_REG_LR)
    stock = StockImage(data)
    assert stock.words(0x115D0378, 7) == (0xF001, 0, 0, 0, 0, 0x103076D1, 0x10307663)
    assert stock.words(0x115D0394, 7) == (0xF002, 0, 0, 0, 0, 0x103077E7, 0x10307663)
    definitions = [('enter', fault) for fault in (
        'none', 'already_open', 'prerequisite', 'enqueue', 'timeout', 'wrong_event', 'bad_result')]
    definitions += [('exit', fault) for fault in ('none', 'prerequisite', 'enqueue', 'timeout')]
    definitions += [('preserve_then_exit', fault) for fault in ('saved_zero', 'saved_one')]
    cases = []
    for operation, fault in definitions:
        context = Context(data, data)
        u = context.u
        context.map(0xC18CA000, 0x1000)
        context.map(0xC20B4248, 0x20000)
        original_byte = int(fault != 'saved_zero')
        u.mem_write(0xC20B4248 + 0x351, bytes((original_byte,)))
        u.mem_write(0xC18CAD24, struct.pack('<2I', 0, 0x1030765D))
        initial_gate = int(operation != 'enter' or fault == 'already_open')
        u.mem_write(0xC18CAD2C, struct.pack('<3I', initial_gate, 0x1030765D, 0))
        u.mem_write(0xC18CAE10, b'\x02')  # Synthetic cached value written by cleanup.
        params = ((0xF064, original_byte, 0, 0, 0) if operation == 'preserve_then_exit'
                  else (0xF001 if operation == 'enter' else 0xF002, 0, 0, 0, 0))
        u.mem_write(RAM, struct.pack('<IHHI5I', 32, 1, 0x94FD, 1, *params))
        calls, responses, writes = [], [], []

        def ret(value=0):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        cleanup = (0x10317D02, 0x10317B96, 0x10333BFE, 0x103185E8,
                   0x103187A0, 0x10319296, 0x10333864, 0x1034B6AE,
                   0x10332CEA, 0x10332BB0, 0x10332E6A, 0x10307748)

        def hook(emulator, address, size, unused):
            a, b, c = [emulator.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            calls.append({'address': hex(address), 'r0': a, 'r1': b})
            if address == 0x1034BB5A:
                ret(0xA001 if fault == 'prerequisite' else 0x2001)
            elif address == 0x101F0BF0:
                assert b == 0x10307677
                if fault != 'enqueue':
                    event = 0x03010003 + a
                    if fault == 'wrong_event':
                        event += 1
                    u.mem_write(0xC18CAD3C, struct.pack('<2I', event, 0 if fault == 'bad_result' else 1))
                ret(0 if fault == 'enqueue' else 1)
            elif address == 0x1034894E:
                assert c == 2000
                ret(1 if fault == 'timeout' else 0)
            elif address == 0x101F5204:
                assert a in (0x126, 0x351)
                ret(original_byte if a == 0x351 else 0)
            elif address == 0x10212648:
                assert a == 0x043D0011 and b == 8
                value, = bytes(emulator.mem_read(0xC20B4248 + 0x351, 1))
                writes.append({'block': 8, 'byte_offset': '0x351', 'value': value})
                ret(1)
            elif address == 0x1045AEB4:
                pointer = emulator.reg_read(UC_ARM_REG_R3)
                assert c == 5
                aux, = struct.unpack('<I', emulator.mem_read(pointer, 4))
                responses.append({'response': hex(b), 'auxiliary_status': hex(aux)})
                ret(1)
            else:
                ret(1)

        for address in cleanup + (0x1034BB5A, 0x101F4E06, 0x103488FC, 0x101F0BF0,
                                   0x1034894E, 0x101F5204, 0x10212648, 0x1045AEB4):
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
        assert context.call(0x1059FAAC, (0, RAM), limit=3000) == 1
        returned = context.call(0x1059FAB2, (0, RAM), limit=6000)
        if operation == 'preserve_then_exit':
            assert returned == 1
            assert bytes(u.mem_read(0xC18CAE10, 1)) == bytes((original_byte,))
            u.mem_write(RAM, struct.pack('<IHHI5I', 32, 1, 0x94FD, 2, 0xF002, 0, 0, 0, 0))
            assert context.call(0x1059FAAC, (0, RAM), limit=3000) == 1
            returned = context.call(0x1059FAB2, (0, RAM), limit=6000)
        final_gate, = struct.unpack('<I', u.mem_read(0xC18CAD2C, 4))
        if operation == 'enter':
            assert final_gate == int(fault in ('none', 'already_open'))
            assert returned == int(fault == 'none')
            assert writes == [] and len(responses) == 1
            expected_aux = 0x2001 if fault == 'none' else (0x201E if fault == 'already_open' else 0xA001)
            assert responses == [{'response': '0x2001', 'auxiliary_status': hex(expected_aux)}]
        elif operation == 'exit':
            assert returned == 1 and final_gate == 0
            assert responses == [{'response': '0x2001', 'auxiliary_status': '0x2001'}]
            assert writes == [{'block': 8, 'byte_offset': '0x351', 'value': 2}]
        else:
            assert returned == 1 and final_gate == 0
            assert responses == [{'response': '0x2001', 'auxiliary_status': '0x2001'}] * 2
            assert writes == [{'block': 8, 'byte_offset': '0x351', 'value': original_byte}] * 2
            assert bytes(u.mem_read(0xC20B4248 + 0x351, 1)) == bytes((original_byte,))
        cases.append(dict(operation=operation, fault=fault, initial_gate=initial_gate,
                          final_gate=final_gate, returned=returned,
                          responses=responses, settings_write_requests=writes, fixture_calls=calls))
    # The region-write handler calls this default-block replacement before
    # requesting the asynchronous settings operation. Run the stock copy only.
    context = Context(data, data)
    context.map(0xC20B4244, 0x20004)
    context.u.mem_write(0xC20B4244, b'\xa5' * 0x20004)
    notifications = []

    def notify(emulator, address, size, unused):
        notifications.append(hex(address))
        emulator.reg_write(UC_ARM_REG_R0, 1)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    context.u.hook_add(UC_HOOK_CODE, notify, begin=0x1008FAF0, end=0x1008FAF0)
    assert context.call(0x101F523E, limit=1000000) == 1
    replaced = bytes(context.u.mem_read(0xC20B4248, 0x1FFFC))
    assert replaced == stock.read(0x114A3F58, 0x1FFFC)
    assert bytes(context.u.mem_read(0xC20B4244, 4)) == b'\xa5' * 4
    assert bytes(context.u.mem_read(0xC20D4244, 4)) == b'\xa5' * 4
    assert notifications == ['0x1008faf0']
    reset_copy = dict(function='0x101f523e', source_rom='0x114a3f58',
                      destination_ram='0xc20b4248', length=0x1FFFC,
                      copied_sha256=sha256(replaced), adjacent_guards_preserved=True,
                      settings_notification_stub=notifications)
    return dict(stock_eg_sha256=sha256(data), cases=cases, region_write_default_copy=reset_copy,
                backend_route=backend_route(data),
                limitations=['All service, settings and asynchronous outcomes are synthetic fixtures.',
                             'No real transition, settings write, USB session or recovery was attempted.',
                             'Stock event dispatch and packet building execute; physical transport/receiver and resource cleanup are not modeled.',
                             'Offset 0x351 meaning and persistence effects have not been established.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Passed 13 offline session/cache cases, stock default-block copy and two backend dispatch cases')


if __name__ == '__main__':
    main()
