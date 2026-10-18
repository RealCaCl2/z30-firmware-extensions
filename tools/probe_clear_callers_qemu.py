"""QEMU-only observer: who calls the queued vgClear wrapper (0x104779F4)?

Renderer clears canvas through the same wrapper, but per-cycle extra clears
(observed ~115 vgClear/s vs renderer events) are suspected to be the layer
clear of the image surface. This probe logs caller LR, args and thread for every
wrapper entry so the callsites can be mapped statically afterwards.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


CLEAR_WRAPPER = 0x104779F4
CAP = 120


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'hits': 0}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{CLEAR_WRAPPER:x},2') == 'OK'
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
                    assert remote.command(f'z1,{CLEAR_WRAPPER:x},2') == 'OK'
                    if pc == CLEAR_WRAPPER:
                        state['hits'] += 1
                        records.append(dict(
                            thread=thread, lr=regs['r14'],
                            r0=regs['r0'], r1=regs['r1'], r2=regs['r2'], r3=regs['r3'],
                            sp=regs['r13']))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{CLEAR_WRAPPER:x},2') == 'OK'
            finally:
                remote.command(f'z1,{CLEAR_WRAPPER:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        by_lr = {}
        for rec in records:
            by_lr.setdefault(rec['lr'], []).append(rec)
        summary = {lr: len(items) for lr, items in by_lr.items()}
        result = dict(probe='queued vgClear wrapper callers',
                      wrapper=hex(CLEAR_WRAPPER), hits=state['hits'],
                      by_lr=summary, records=records[:CAP],
                      notes='QEMU-only; renderer internal clears call vgClear directly, '
                            'so wrapper callers are other modules (display/layer)')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
    finally:
        s.close()


if __name__ == '__main__':
    main()
