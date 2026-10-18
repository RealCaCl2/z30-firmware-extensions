"""QEMU-only observer: the scaled surface draw (0x10479022) argument structs.

The function at 0x10479022 performs a scaled vgDrawImage/vgDrawPath pass and is
the only draw path besides the UI renderer, so it is a candidate for the
image-surface -> overlay-bank compositor. Dump its argument struct on entry and
look for surface pointers in the fields.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {0x10479022: 'draw-scaled'}
CAP = 16

KNOWN = {
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=2.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'hits': 0, 'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or state['hits'] >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in SITES:
                assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            try:
                while time.monotonic() < deadline and state['hits'] < CAP:
                    remote.send('c')
                    remote.sock.settimeout(max(.01, deadline - time.monotonic()))
                    try:
                        packet = remote.receive()
                    except socket.timeout:
                        remote.sock.sendall(b'\x03')
                        remote.sock.settimeout(5)
                        self.packet = remote.receive()
                        break
                    self.packet = packet
                    thread = packet.split('thread:')[1].split(';')[0]
                    assert remote.command('Hg' + thread) == 'OK'
                    regs = remote.registers()
                    pc = int(regs['r15'], 16)
                    for address in SITES:
                        assert remote.command('z1,{:x},2'.format(address)) == 'OK'
                    if pc in SITES:
                        state['hits'] += 1
                        arg = int(regs['r0'], 16)
                        rec = dict(n=len(records), thread=thread, at=hex(pc),
                                   lr=hex(int(regs['r14'], 16)),
                                   sp=hex(int(regs['r13'], 16)),
                                   arg=hex(arg))
                        if arg:
                            raw = remote.memory(arg, 0xA0)
                            rec['arg_hex'] = raw.hex()
                            words = []
                            for off in range(0, 0xA0, 4):
                                value = int.from_bytes(raw[off:off + 4], 'little')
                                words.append(dict(off=hex(off), value=hex(value),
                                                  name=KNOWN.get(value, '')))
                            rec['arg_words'] = words
                        # stack arguments of the BL caller: handler frame words.
                        sp = int(regs['r13'], 16)
                        raw = remote.memory(sp, 0x20)
                        rec['stack_hex'] = raw.hex()
                        records.append(rec)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in SITES:
                        assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            finally:
                for address in SITES:
                    remote.command('z1,{:x},2'.format(address))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(0.2)
        state['armed'] = True
        s.run_for(a.seconds)
        first = records[0]['arg_words'] if records else []
        result = dict(probe='scaled surface draw argument structs', sites=SITES,
                      hits=state['hits'], records=records,
                      notes='QEMU-only; arg struct at r0 on entry to 0x10479022')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('hits', state['hits'])
        for rec in records[:8]:
            print(json.dumps({k: rec[k] for k in ('n', 'thread', 'lr', 'arg')}))
            print('  arg', [w['value'] for w in rec['arg_words'][:12]])
    finally:
        s.close()


if __name__ == '__main__':
    main()
