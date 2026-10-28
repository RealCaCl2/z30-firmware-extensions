"""Probe stock save-caller control flow with synthetic objects/services.

The save dispatcher, format query, completion notification and delay are
intercepted. This is not proof of resource release or valid capture objects.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR

CALLER = 0x1009F92C
DISPATCH, CLASSIFY, NOTIFY, QUERY, DELAY = 0x1033DF28, 0x10338686, 0x10146DE6, 0x1033872A, 0x1021994A
OBJECT, MANAGER, INFO = RAM + 0x10000, RAM + 0x20000, RAM + 0x30000


def probe(stock, save_status, image=None):
    context = Context(stock, stock if image is None else image)
    u = context.u
    events = []

    def word(address, value):
        u.mem_write(address, struct.pack('<I', value))

    word(OBJECT + 0xC, MANAGER)
    word(MANAGER + 0x3A4, INFO)
    u.mem_write(INFO + 0x25BA, b'\x01')
    word(MANAGER + 0x320, 1)
    word(OBJECT + 0x2C0, 1)
    entry = OBJECT + 0x238 + 0x90
    word(entry + 0x30, 0x1357)
    word(MANAGER + 0x334, 0x2468)
    # Address comes from the original PC-relative literal at 0x1009F96A.
    global_address = context.stock.words(0x1009FD08, 1)[0]
    context.map(global_address, 0x30)
    u.mem_write(global_address, bytes(0x30))

    def hook(emulator, address, size, data):
        r0, r1, r2 = [emulator.reg_read(r) for r in
                      (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        if address == CLASSIFY:
            result = 0  # Synthetic format class, not JPEG binding.
        elif address == DISPATCH:
            events.append(dict(kind='save_dispatch', selector=r0, mode=r1,
                               object=hex(r2)))
            result = save_status
        elif address == NOTIFY:
            events.append(dict(kind='completion_candidate', handle=hex(r0), code=r1))
            result = 0
        elif address == QUERY:
            result = 6  # Select original branch which avoids an unknown callback.
        elif address == DELAY:
            events.append(dict(kind='delay', value=r0))
            result = 0
        else:
            raise AssertionError('Unexpected interception')
        emulator.reg_write(UC_ARM_REG_R0, result)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    for address in (DISPATCH, CLASSIFY, NOTIFY, QUERY, DELAY):
        u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
    result = context.call(CALLER, (OBJECT,))
    pending = struct.unpack('<I', u.mem_read(OBJECT + 0x2C0, 4))[0]
    handle = struct.unpack('<I', u.mem_read(MANAGER + 0x334, 4))[0]
    if save_status == 2:
        assert result == 1 and pending == 1 and handle == 0x2468
        assert [event['kind'] for event in events] == ['save_dispatch', 'delay']
        assert events[-1]['value'] == 100
    else:
        assert result == 0 and pending == 0 and handle == 0
        assert [event['kind'] for event in events] == ['save_dispatch', 'completion_candidate']
        assert events[-1] == dict(kind='completion_candidate', handle='0x2468', code=15)
    assert events[0]['selector'] == 1 and events[0]['object'] == hex(entry + 0x30)
    return dict(save_status=save_status, caller_return=result, pending_count=pending,
                handle_after=hex(handle), events=events, passed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, default=Path('build/research/precapture-core/lifecycle.json'))
    args = parser.parse_args()
    if args.output.resolve() == args.source.resolve():
        raise ValueError('Output must not overwrite source')
    source = args.source.read_bytes()
    stock = source if len(source) == 33939514 else extract_eg(source)
    StockImage(stock)
    cases = [probe(stock, status) for status in (0, 2, 3)]
    report = dict(source_sha256=sha256(stock), caller=hex(CALLER), cases=cases,
                  passed=len(cases), real_capture_object_verified=False,
                  completion_resource_semantics_verified=False,
                  substitutions=['save dispatcher result', 'format class query',
                                 'completion candidate', 'state query', 'delay service'],
                  limitations=['Completion code 15 is observed, not proven to free a JPEG pool.',
                               'Status 2 retains a pending slot in this caller, not a safe general deferral API.',
                               'No real capture, producer task, DMA or filesystem execution.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
