"""QEMU-only observer: who writes the image surface and the UI canvas?

Hardware write watchpoints on one sampled word of each surface report the PC of
every writer, so the per-cycle clear/regenerate step can be named statically.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


WIDTH = 720
IMAGE = 0xD0959600
CANVAS = 0xD045F200
PROBES = [('image', IMAGE + (240 * WIDTH + 350) * 4),
          ('canvas', CANVAS + (240 * WIDTH + 350) * 4)]
CAP = 160


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=0.8)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'hits': 0}

    def arm(remote):
        for name, address in PROBES:
            assert remote.command(f'Z2,{address:x},4') == 'OK'

    def disarm(remote):
        for name, address in PROBES:
            remote.command(f'z2,{address:x},4')

    def classify(remote, thread):
        assert remote.command('Hg' + thread) == 'OK'
        regs = remote.registers()
        pc = int(regs['r15'], 16)
        lr = int(regs['r14'], 16)
        state['hits'] += 1
        records.append(dict(thread=thread, pc=hex(pc), lr=hex(lr),
                            r0=regs['r0'], r1=regs['r1'], r2=regs['r2'], r3=regs['r3']))

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            arm(remote)
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
                    classify(remote, thread)
                    assert remote.command('Hc' + thread) == 'OK'
                    # Re-arm: QEMU reports the watchpoint hit then continues.
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
            finally:
                disarm(remote)

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        by_pc = {}
        for rec in records:
            by_pc.setdefault(rec['pc'], []).append(rec)
        summary = {pc: dict(count=len(items), lr=items[0]['lr']) for pc, items in by_pc.items()}
        result = dict(probe='surface write watchpoints', probes=PROBES,
                      hits=state['hits'], by_pc=summary, records=records[:CAP],
                      notes='QEMU-only; watchpoint stops happen after the write, PC is the writer')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
    finally:
        s.close()


if __name__ == '__main__':
    main()
