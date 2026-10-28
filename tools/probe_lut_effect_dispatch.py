"""Execute stock effect selection and completion dispatch with synthetic arguments.

Stops at effect callbacks, message recycling and event services. No effect
algorithm, image, real scheduler, codec, DMA, filesystem or camera is executed.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import sha256
from inspect_lut_postprocess import effect_records
from native_runtime import Context, RAM
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR, UC_ARM_REG_PC

PARAM, MESSAGE, CALLBACK = RAM + 0x1000, RAM + 0x2000, RAM + 0x9000
RECYCLE, EVENT = 0x10211F5E, 0x10199E04


def probe(data):
    records = effect_records(data)
    context = Context(data, data)
    u = context.u
    callbacks = {int(r[key], 16) & ~1 for r in records
                 for key in ('optional_callback', 'query_callback') if int(r[key], 16)}
    calls = []

    def boundary(uc, address, size, unused):
        if address not in callbacks | {CALLBACK, RECYCLE, EVENT}:
            return
        r0, r1 = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1)]
        calls.append(dict(address=hex(address), r0=hex(r0), r1=hex(r1)))
        uc.reg_write(UC_ARM_REG_R0, 0x55)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    u.hook_add(UC_HOOK_CODE, boundary)
    rows = []
    for record in records:
        index = record['index']
        assert context.call(int(record['getter'], 16)) == int(record['descriptor'], 16)
        calls.clear()
        result = context.call(0x100CCAB0, (index, PARAM))
        expected = int(record['optional_callback'], 16) & ~1
        assert result == (0x55 if expected else 0)
        assert len(calls) == bool(expected)
        if expected:
            assert calls[0]['address'] == hex(expected) and calls[0]['r0'] == hex(PARAM)
        optional_calls = list(calls)
        calls.clear()
        result = context.call(0x100CCAD4, (index, PARAM))
        expected = int(record['query_callback'], 16) & ~1
        assert result == 0x55 and len(calls) == 1
        assert calls[0]['address'] == hex(expected) and calls[0]['r0'] == hex(PARAM)
        query_calls = list(calls)
        # Synthetic completion message, not the processing worker at 0x100CC910.
        # Callback is intercepted, never a LUT kernel.
        u.mem_write(PARAM, struct.pack('<I', CALLBACK | 1))
        u.mem_write(MESSAGE, bytes(0x1C))
        u.mem_write(MESSAGE + 0x10, struct.pack('<II', index, PARAM))
        calls.clear()
        context.call(0x100CCA12, (0, MESSAGE))
        assert [c['address'] for c in calls] == [hex(CALLBACK), hex(RECYCLE)]
        assert calls[0]['r0'] == record['label'] and calls[0]['r1'] == hex(PARAM)
        assert calls[1]['r0'] == hex(MESSAGE)
        rows.append(dict(index=index, getter_return=record['descriptor'],
                         optional_calls=optional_calls, query_calls=query_calls,
                         worker_calls=list(calls)))
    calls.clear()
    assert context.call(0x100CCAB0, (20, PARAM)) == 0 and not calls
    assert context.call(0x100CCAB0, (0xFFFFFFFF, PARAM)) == 0 and not calls
    u.mem_write(PARAM, bytes(4))
    calls.clear()
    context.call(0x100CCA12, (0, MESSAGE))
    assert [c['address'] for c in calls] == [hex(EVENT), hex(RECYCLE)]
    assert calls[0]['r1'] == '0x1' and calls[1]['r0'] == hex(MESSAGE)
    return dict(source_sha256=sha256(data), camera_access=False, cases=rows,
                bounds_cases=2, zero_callback_worker_calls=list(calls),
                native_calls=83, preserved_registers_and_stack_checked=True,
                boundaries_replaced=['Effect callbacks', 'Caller-supplied worker callback',
                                     'Message recycling', 'Event service'],
                limitations=[
                    'All parameter/message memory is synthetic; no decoded pixels are present.',
                    'Native effect callbacks are intercepted before their bodies execute.',
                    'Request creation, queues, tasks, concurrency and callback lifetimes are not modeled.',
                    'No retouch-operation identity, LUT execution, JPEG codec or DCF-save proof.',
                    'Query and worker paths lack the optional wrapper\'s index bound; only 0..19 tested.',
                ])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/dispatch.json'))
    args = p.parse_args()
    if args.output.resolve() == args.source.resolve():
        raise ValueError('Output must differ from source')
    report = probe(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS: 20 getters, 20 optional selections, 20 queries, 21 completion dispatches, 2 bounds cases')
    print('No image, effect body, codec, scheduler or camera validation.')


if __name__ == '__main__':
    main()
