"""QEMU-only observation of GPU copy submissions (0x1055D112).

The overlay bank is built per display cycle from two GEOMETRY copies: the
image surface first, then the UI canvas. To insert a mask layer between them we
need the exact submission descriptor, its source/destination fields, and the
caller sites that produce the two copies.

This probe stops at the copy submission entry 0x1055D112, records r0/r1, the
caller LR and the 0x68-byte descriptor at r0, and continues. No guest code is
patched and nothing is injected.

QEMU-only; the colour bars are a fixture.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
DESCRIPTOR = 0x68


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.5)
    p.add_argument('--limit', type=int, default=120)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{SUBMIT:x},2') == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < a.limit:
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
                    assert pc == SUBMIT, 'Unexpected guest stop: ' + packet
                    r0 = int(regs['r0'], 16)
                    r1 = int(regs['r1'], 16)
                    lr = int(regs['r14'], 16)
                    r2 = int(regs['r2'], 16)
                    r3 = int(regs['r3'], 16)
                    body = remote.memory(r0, DESCRIPTOR) if r0 else b''
                    words = [int.from_bytes(body[i:i + 4], 'little')
                             for i in range(0, min(len(body), DESCRIPTOR) - 3, 4)]
                    records.append(dict(
                        n=len(records), thread=thread, caller=hex(lr),
                        r0=hex(r0), r1=hex(r1), r2=hex(r2), r3=hex(r3),
                        descriptor=body.hex(), words=[hex(w) for w in words],
                        pointers=[hex(w) for w in words
                                  if 0xC0000000 <= w < 0xF0000000],
                        stack=remote.memory(int(regs['r13'], 16), 16).hex()))
                    assert remote.command(f'z1,{SUBMIT:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{SUBMIT:x},2') == 'OK'
            finally:
                remote.command(f'z1,{SUBMIT:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(.3)
        s.run_for(a.seconds)
        (out / 'context.json').write_text(json.dumps(
            dict(probe='copy-submit observation', stop=hex(SUBMIT),
                 descriptor_bytes=DESCRIPTOR, records=records), indent=2) + '\n')
        print('records:', len(records), flush=True)
        for record in records:
            print(record['n'], record['thread'], 'caller', record['caller'],
                  'r0', record['r0'], 'r1', record['r1'],
                  'pointers', record['pointers'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
