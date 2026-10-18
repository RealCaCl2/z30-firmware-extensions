"""QEMU-only: which surface does each renderer clear target?

The renderer clears a surface just before drawing the element (vgClear at
0x104785D4, next instruction 0x104785D8). Stop at 0x104785D8 and use sentinel
stamps: after the clear, write 0xDEADBEEF into one pixel of the UI canvas and one
pixel of the image surface, resume, and at the next stop check which sentinel was
cleared away. The cleared one is the surface that the renderer's vgClear bound
as its current VG target.

QEMU-only; read-only guest code.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


CLEAR_RETURN = 0x104785D8
CANVAS = 0xD045F200
IMAGE = 0xD0959600
SAMPLES = [('center', 100, 240), ('right', 600, 240), ('low', 350, 420)]
STAMP = 0xDEADBEEF
CAP = 45


def word_address(base, x, y):
    return base + (y * 720 + x) * 4


def read_word(remote, address):
    reply = remote.command(f'm{address:x},4').strip()
    return int(reply, 16) if len(reply) >= 8 else None


def write_word(remote, address, value):
    assert remote.command(f'M{address:x},4:{value:08x}') == 'OK'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.6)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    pending = {}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{CLEAR_RETURN:x},2') == 'OK'
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
                    assert remote.command(f'z1,{CLEAR_RETURN:x},2') == 'OK'
                    if pc == CLEAR_RETURN:
                        entry = dict(thread=thread, r4=regs['r4'], lr=regs['r14'],
                                     seen={}, stamps={})
                        for name, x, y in SAMPLES:
                            for surface, base in (('canvas', CANVAS), ('image', IMAGE)):
                                address = word_address(base, x, y)
                                entry['seen'][f'{surface}-{name}'] = read_word(remote, address)
                        for name, x, y in SAMPLES:
                            for surface, base in (('canvas', CANVAS), ('image', IMAGE)):
                                address = word_address(base, x, y)
                                write_word(remote, address, STAMP)
                                entry['stamps'][f'{surface}-{name}'] = hex(address)
                        records.append(entry)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{CLEAR_RETURN:x},2') == 'OK'
            finally:
                remote.command(f'z1,{CLEAR_RETURN:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        result = dict(probe='renderer clear target surface identification',
                      clear_return=hex(CLEAR_RETURN), cap=CAP, records=records,
                      notes='previous stop stamped 0xDEADBEEF into canvas+image; '
                            'value 0 at next stop means that surface was the clear target')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for index, rec in enumerate(records):
            print(index, rec['thread'], rec['r4'], {k: hex(v) if v is not None else None
                                                    for k, v in rec['seen'].items()}, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
