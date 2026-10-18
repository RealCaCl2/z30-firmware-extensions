"""QEMU-only observer: what is inside the image surface at copy-submit time?

Copy (2) in the display cycle reads 0xD0959600 (720x480 RGBA) before the UI
canvas copy.  This probe samples that surface at the moment the copy is
submitted and compares it with the UI canvas, so the layer role can be named
(video frame, scratch, or empty).

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
IMAGE = 0xD0959600
CANVAS = 0xD045F200
WIDTH, HEIGHT = 720, 480
CAP = 8
SAMPLES = [('center', 350, 240), ('left', 40, 150), ('top', 300, 60),
           ('bottom', 610, 455), ('bar_yellow', 120, 240), ('bar_blue', 600, 240)]


def rgba(remote, base, x, y):
    raw = remote.memory(base + (y * WIDTH + x) * 4, 4)
    return '.'.join(f'{b:02x}' for b in raw)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.2)
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
            assert remote.command(f'Z1,{SUBMIT:x},2') == 'OK'
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
                    assert remote.command(f'z1,{SUBMIT:x},2') == 'OK'
                    if pc == SUBMIT:
                        state['hits'] += 1
                        records.append(dict(
                            thread=thread, lr=regs['r14'], desc=regs['r0'],
                            image={n: rgba(remote, IMAGE, x, y) for n, x, y in SAMPLES},
                            canvas={n: rgba(remote, CANVAS, x, y) for n, x, y in SAMPLES}))
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
        s.run_for(a.seconds)
        result = dict(probe='copy submit surface content', submit=hex(SUBMIT),
                      hits=state['hits'], samples=[n for n, _, _ in SAMPLES],
                      records=records[:CAP],
                      notes='QEMU-only; image surface vs UI canvas content at copy submit')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for i, rec in enumerate(records):
            print(i, rec['lr'], 'image', rec['image'])
            print('  canvas', rec['canvas'])
    finally:
        s.close()


if __name__ == '__main__':
    main()
