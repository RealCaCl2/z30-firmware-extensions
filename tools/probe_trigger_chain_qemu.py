"""QEMU-only observer: GPU trigger call chain via stack return addresses.

Break at the GPU FIFO trigger 0x10581A4A (last guest step before the model
executes a draw) and dump the guest stack words. Every return address in the
code range reconstructs the call chain that requested the draw, which finally
names the live-view compositing caller.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


TRIGGER = 0x10581A4A
CAP = 12
CODE_LO, CODE_HI = 0x10020000, 0x12240000


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.5)
    p.add_argument('--depth', type=int, default=0xC0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or len(records) >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command('Z1,{:x},2'.format(TRIGGER)) == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < CAP:
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
                    assert remote.command('z1,{:x},2'.format(TRIGGER)) == 'OK'
                    if pc == TRIGGER:
                        sp = int(regs['r13'], 16)
                        raw = remote.memory(sp, a.depth)
                        words = [int.from_bytes(raw[i:i + 4], 'little')
                                 for i in range(0, a.depth, 4)]
                        code = [hex(w) for w in words if CODE_LO <= w < CODE_HI]
                        records.append(dict(n=len(records), thread=thread,
                                            lr=hex(int(regs['r14'], 16)),
                                            sp=hex(sp), r0=hex(int(regs['r0'], 16)),
                                            r1=hex(int(regs['r1'], 16)),
                                            r2=hex(int(regs['r2'], 16)),
                                            code_addrs=code,
                                            stack=[hex(w) for w in words]))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command('Z1,{:x},2'.format(TRIGGER)) == 'OK'
            finally:
                remote.command('z1,{:x},2'.format(TRIGGER))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(0.2)
        state['armed'] = True
        s.run_for(a.seconds)
        print('triggers', len(records))
        for rec in records:
            print(rec['n'], 'lr', rec['lr'], 'code', rec['code_addrs'][:10])
        (out / 'context.json').write_text(json.dumps(
            dict(probe='gpu trigger stack call chain', trigger=hex(TRIGGER),
                 triggers=len(records), records=records), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
