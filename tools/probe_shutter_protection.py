"""Execute bounded stock logic offline with explicit synthetic service results.

No camera, GPIO, timer, shutter mechanism, power rail or real OS scheduling.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256


def probe(data, trace):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR
    stock = StockImage(data)
    assert trace['stock_eg_sha256'] == sha256(data)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
    def ret(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    # Run stock controller lookup and getters, stopping at the real event handlers.
    context = Context(data, data)
    seen = []
    def sink(u, address, size, ident):
        seen.append(ident)
        ret(u)
    for controller in trace['controllers']:
        address = int(controller['event_handler'], 16) & ~1
        context.u.hook_add(UC_HOOK_CODE, sink, user_data=controller['id'], begin=address, end=address)
    routes = []
    for command in trace['commands']:
        seen.clear()
        message = RAM+0x100
        context.u.mem_write(message, struct.pack('<7I', 0, 0, 0, 0, command['id'], 0, 0))
        context.call(0x1005F4F6, (message, 1), limit=3000)
        assert seen == command['route'], (command, seen)
        routes.append(dict(id=command['id'], name=command['name'], controllers=list(seen)))

    # Exercise actual state update/table/TBB mapping. Only the downstream action is substituted.
    context = Context(data, data)
    actions = []
    injected = [1]
    def action(u, address, size, unused):
        actions.append([u.reg_read(r) for r in regs[:2]])
        ret(u, injected[0])
    context.u.hook_add(UC_HOOK_CODE, action, begin=0x101F8D1E, end=0x101F8D1E)
    matrix = []
    state = 0xC18A611A
    action_pairs = [(5,8), (2,1), (5,2), (5,3), (3,4), (5,6), (5,7), (4,5)]
    for prior in range(8):
        for event in range(8):
            context.u.mem_write(state, bytes([prior]))
            context.u.mem_write(RAM+0x100, bytes([event]))
            actions.clear()
            returned = context.call(0x101F8F60, (RAM+0x100,), limit=500)
            after = bytes(context.u.mem_read(state, 1))[0]
            expected = trace['capture_transition_table'][prior*8+event]
            assert after == expected
            assert actions == ([] if prior == expected else [list(action_pairs[event])])
            assert returned == int(prior != expected)
            matrix.append(dict(prior=prior, event=event, after=after, returned=returned, actions=list(actions)))
    context.u.mem_write(state, b'\x00')
    context.u.mem_write(RAM+0x100, b'\x01')
    injected[0] = 0
    actions.clear()
    failed_result = context.call(0x101F8F60, (RAM+0x100,), limit=500)
    failed_after = bytes(context.u.mem_read(state, 1))[0]
    assert failed_result == 0 and failed_after == 1

    cycles = []
    cases = [('zero',0,None,0), ('one',1,None,1), ('three',3,None,3),
             ('prepare_rejected',1,('action',1),0), ('wait_failed',1,('wait',1),0),
             ('busy_after_wait',1,('busy',1),0), ('post4_rejected',1,('action',2),0),
             ('post5_rejected',1,('action',3),0), ('second_wait_failed',3,('wait',2),1)]
    for name, requested, failure, expected in cases:
        context = Context(data, data)
        context.map(0xC239B000, 0x1000)
        events, counts = [], dict(action=0, wait=0, busy=0)
        def hook(u, address, size, unused):
            r0,r1,r2,r3 = [u.reg_read(r) for r in regs]
            if address == 0x101F8D1E:
                kind = 'action'
                counts[kind] += 1
                events.append(dict(kind=kind, arguments=[r0,r1]))
                ret(u, int(failure != (kind, counts[kind])))
            elif address == 0x100F2782:
                events.append(dict(kind='mechanical_request_intercepted', arguments=[r0,r1]))
                ret(u)
            elif address == 0x10199E0A:
                events.append(dict(kind='event_clear_intercepted', arguments=[r0,r1]))
                ret(u, 1)
            elif address == 0x10199ECA:
                counts['wait'] += 1
                events.append(dict(kind='wait_intercepted', arguments=[r0,r1,r3]))
                u.mem_write(r2, struct.pack('<I',r1))
                ret(u, int(failure != ('wait', counts['wait'])))
            elif address == 0x101F8DB6:
                counts['busy'] += 1
                ret(u, int(failure == ('busy', counts['busy'])))
        for address in (0x101F8D1E,0x100F2782,0x10199E0A,0x10199ECA,0x101F8DB6):
            context.u.hook_add(UC_HOOK_CODE, hook, begin=address,end=address)
        returned = context.call(0x101FCFF8, (requested,RAM+0x100),limit=10000)
        completed, = struct.unpack('<I',context.u.mem_read(RAM+0x100,4))
        assert completed == expected, (name,completed,events)
        cycles.append(dict(case=name, requested=requested, injected_failure=failure,
                           completed=completed, returned=returned,
                           final_abstract_state=bytes(context.u.mem_read(state,1))[0], events=events))

    # Real aggregate loop; all twenty service callbacks are intercepted.
    context = Context(data, data)
    context.map(0xC207B000,0x1000)
    callback_order = []
    callbacks = [int(x,16)&~1 for x in trace['shutdown_callbacks']]
    def callback(u,address,size,unused):
        callback_order.append(callbacks.index(address))
        ret(u)
    for address in set(callbacks):
        context.u.hook_add(UC_HOOK_CODE,callback,begin=address,end=address)
    order_cases = []
    for direction in (1,0):
        callback_order.clear()
        context.call(0x1026E8A6,(direction,0),limit=2000)
        expected = list(range(20)) if direction else list(reversed(range(20)))
        assert callback_order == expected
        order_cases.append(dict(direction=direction, service_slot_order=list(callback_order)))

    # Two stock output wrappers. Lower timer/GPIO services are intercepted;
    # synthetic handles intentionally carry no physical channel meaning.
    gates = []
    for entry, mask, inhibit_offset, channel in ((0x101B43AC,8,0xA8,0),
                                                 (0x101B4248,16,0xAC,1)):
        context = Context(data,data)
        events = []
        def output_hook(u,address,size,unused):
            values = [u.reg_read(r) for r in regs]
            if address == 0x101D42F0:
                events.append(dict(service='gpio_intercepted',handle=values[0],value=values[1]))
            elif address == 0x1012604C:
                payload = bytes(u.mem_read(values[1],44))
                enabled, = struct.unpack_from('<I',payload,40)
                events.append(dict(service='timer_config_intercepted',mask=values[0],enabled=enabled))
            else:
                events.append(dict(service='timer_start_intercepted',mask=values[0]))
            ret(u)
        for address in (0x101D42F0,0x1012604C,0x10125FA6):
            context.u.hook_add(UC_HOOK_CODE,output_hook,begin=address,end=address)
        context.u.mem_write(0xC18A6440,struct.pack('<2I',0xAB000001,0xAB000002))
        for enabled,inhibited,has_gpio in ((0,0,1),(1,0,1),(1,1,1),(1,0,0)):
            events.clear()
            context.u.mem_write(0xC18A6408+inhibit_offset,struct.pack('<I',inhibited))
            context.u.mem_write(0xC18A6429,bytes([has_gpio]))
            context.call(entry,(enabled,),limit=500)
            effective=enabled if not inhibited else 0
            expected=[dict(service='timer_config_intercepted',mask=mask,enabled=effective),
                      dict(service='timer_start_intercepted',mask=mask)]
            if has_gpio:
                expected.append(dict(service='gpio_intercepted',handle=0xAB000001+channel,value=effective))
            assert events == expected,(hex(entry),events,expected)
            gates.append(dict(entry=hex(entry),input=enabled,inhibited=inhibited,gpio_available=has_gpio,
                              events=list(events)))
    return dict(stock_eg_sha256=sha256(data),routes=routes,state_transitions=matrix,
                rejected_action=dict(prior=0,event=1,returned=failed_result,after=failed_after),
                drive_cycles=cycles,aggregate_order=order_cases,output_gate_cases=gates,
                limitations=['Synthetic inputs and injected service success/failure only.',
                             'No real controller handlers run in route fixtures.',
                             'No downstream mechanics, GPIO, timer, power or OS model.',
                             'Abstract states are not physical open/closed positions.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path('Z_30_0120.bin'))
    p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.resolve() in (args.source.resolve(),args.trace.resolve()):
        raise ValueError('Output must differ from inputs')
    raw=args.source.read_bytes()
    data=raw if len(raw)==33939514 else extract_eg(raw)
    result=probe(data,json.loads(args.trace.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2)
        f.write('\n')
    print(f"Passed {len(result['routes'])} routes, 64 transitions, 1 rejected transition, 9 cycle, 2 aggregate-order and 8 output-gate cases.")


if __name__=='__main__':
    main()
