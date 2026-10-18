"""QEMU-only: map copy wrappers to their source/target plane objects.

Stops at the three copy wrappers (0x10514CFC, 0x10514D3E, 0x10514D8C) and at the
common submit 0x1055D112.  At each stop, read candidate plane objects (r2/r3 and,
for the submit, the descriptor fields) plus the +0x80..+0x9c words so we can see
which object carries the 720x480 surface address (d0959600 image surface vs
d045f200 UI canvas).  Read-only.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


WRAPPERS = {
    0x10514CFC: 'copy-0a03',
    0x10514D3E: 'copy-0a01',
    0x10514D8C: 'copy-rect',
}
SUBMIT = 0x1055D112
CAP = 90
KNOWN = {0xD045F200: 'ui-canvas', 0xD0959600: 'image-surface', 0xD2BFD600: 'canvas3',
         0xECCA7000: 'bank0', 0xECEE7000: 'bank1', 0xED127000: 'bank2'}


def dump_object(remote, address, words=16):
    reply = remote.command(f'm{address:x},{words * 4:x}').strip()
    try:
        data = bytes.fromhex(reply)
    except ValueError:
        return None
    values = [int.from_bytes(data[i:i + 4], 'little') for i in range(0, len(data) - 3, 4)]
    return [hex(v) for v in values]


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
    breakpoints = sorted([*WRAPPERS, SUBMIT])

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            for address in breakpoints:
                assert remote.command(f'Z1,{address:x},2') == 'OK'
            deadline = time.monotonic() + seconds
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
                    for address in breakpoints:
                        assert remote.command(f'z1,{address:x},2') == 'OK'
                    if pc in WRAPPERS or pc == SUBMIT:
                        entry = dict(site=hex(pc), name=WRAPPERS.get(pc, 'submit'),
                                     thread=thread, lr=regs['r14'],
                                     r0=regs['r0'], r1=regs['r1'],
                                     r2=regs['r2'], r3=regs['r3'])
                        for label in ('r2', 'r3'):
                            value = int(regs[label], 16)
                            if 0xC0000000 <= value < 0xE0000000:
                                entry[label] = {
                                    'object': hex(value),
                                    'words00': dump_object(remote, value, 16),
                                    'words80': dump_object(remote, value + 0x80, 10),
                                }
                        if pc == SUBMIT:
                            desc = int(regs['r0'], 16)
                            if 0xC0000000 <= desc < 0xE0000000:
                                entry['descriptor'] = dump_object(remote, desc, 26)
                        records.append(entry)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in breakpoints:
                        assert remote.command(f'Z1,{address:x},2') == 'OK'
            finally:
                for address in breakpoints:
                    remote.command(f'z1,{address:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        result = dict(probe='copy wrapper plane mapping', wrappers={hex(k): v for k, v in WRAPPERS.items()},
                      submit=hex(SUBMIT), known={hex(k): v for k, v in KNOWN.items()}, records=records)
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        def field84(entry, label):
            try:
                value = int(entry[label]['words80'][1], 16)
            except (KeyError, IndexError, TypeError, ValueError):
                return '?'
            return f'{value:#010x} {KNOWN.get(value, "")}'.strip()

        for index, rec in enumerate(records):
            print(index, rec['name'], rec['thread'], 'lr=' + rec['lr'],
                  'r2+84=' + field84(rec, 'r2'), 'r3+84=' + field84(rec, 'r3'),
                  flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
